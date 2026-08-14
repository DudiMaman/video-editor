#!/usr/bin/env python3
"""Batch worker for GitHub Actions.

Reads a JSON array of processing requests (from --requests or the
REQUESTS_JSON env var), processes each one with the same service modules the
local server uses (trim -> normalize outro -> concat -> caption), and writes
finished videos plus summary.json / notes.md into the output directory.
A failed request never stops its siblings.

Request shape:
  {"asset": "<asset id from assets.json>",
   "outro": "outros/<asset>/<file>",         # must be listed for that asset
   "intro": "intros/<asset>/<file>",         # optional, prepended before the clip
   "start_seconds": 5,                       # optional, keep from this second
   "cut_seconds": 20,                        # optional, keep up to this second
                                             # (omitted -> keep until the end)
   "source_url": "https://..."}              # or "source_path": "inbox/..."
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services import captions, downloader  # noqa: E402
from app.services import ffmpeg as ff  # noqa: E402


def transcript_key(req: dict) -> str:
    """Stable key shared with the frontend for transcript lookups."""
    src = req.get("source_url") or req.get("source_path") or ""
    m = re.search(r"/video/(\d+)", src)
    if m:
        return m.group(1)
    if req.get("source_path"):
        return Path(req["source_path"]).stem
    return hashlib.sha1(src.encode()).hexdigest()[:16]


def resolve_source(req: dict, work: Path) -> Path:
    if req.get("source_url"):
        return downloader.download(req["source_url"], work / "src")
    if req.get("source_path"):
        source = (REPO_ROOT / req["source_path"]).resolve()
        if not source.is_relative_to(REPO_ROOT):
            raise ValueError("source_path escapes the repository")
        if not source.exists():
            raise ValueError(f"source file missing from repo: {req['source_path']}")
        return source
    raise ValueError("request needs source_url or source_path")


# Common words used to confirm an OCR line is real caption text rather than
# noise from decorative textures. A word also passes if it simply looks like
# a plausible word (has a vowel and a consonant) - the dictionary just
# guarantees a floor of recognisable content per line.
_COMMON = set("""the a an and or to of in on for with your you it is are be own make
plant plants tag tags water soil pot pots leaf leaves cut off stem cuttings place
put change every day days new home homes fill gap gaps mix propagate divide grow
roots have grown about two inches can repot little from time mist top right method
download now keep alive app store google play once more this that here how what
when into over under out up don worry buddy soon see shoots sprouting them their
get least just one instead care tips scan identify diagnose free link bio check
hacks add remove lower spray drops iodine potassium fertilize fertilizer light
houseplant houseplants indoor outdoor snake pothos monstera basil tomato""".split())


def _is_word(w: str) -> bool:
    w = re.sub(r"[^A-Za-z']", "", w).lower()
    if len(w) < 2:
        return False
    return (w in _COMMON or
            (len(w) >= 3 and re.search(r"[aeiou]", w)
             and re.search(r"[bcdfghjklmnpqrstvwxyz]", w)))


def _line_key(l: str) -> str:
    return re.sub(r"[^a-z]", "", l.lower())


def _similar(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    if a == b:
        return True
    sa, sb = set(a.split()), set(b.split())
    return len(sa & sb) >= 0.5 * max(len(sa), len(sb), 1)


def _distinct_real(txt: str) -> int:
    return len({re.sub(r"[^a-z]", "", w.lower())
                for w in txt.split() if _is_word(w)})


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", w.lower())


def _dedup_words(txt: str) -> str:
    """Clean the mess an unstable OCR read of a busy end-card produces: drop
    stray single-letter / punctuation fragments, then collapse immediate
    duplicate tokens and repeated 2-5 word phrases (punctuation-insensitive)."""
    kept = []
    for w in txt.split():
        nl = _norm(w)
        if not nl:                                        # pure punctuation
            continue
        if len(nl) == 1 and nl not in ("a", "i"):         # stray letters
            continue
        if len(nl) <= 2 and not nl.isdigit() and not _is_word(w):
            continue
        kept.append(w)
    out = []
    for w in kept:                                        # immediate dupes
        if out and _norm(out[-1]) == _norm(w):
            continue
        out.append(w)
    changed = True
    while changed:                                        # repeated phrases
        changed = False
        for win in (5, 4, 3, 2):
            res, i = [], 0
            while i < len(out):
                a = [_norm(x) for x in out[i:i + win]]
                if len(a) == win and a == [_norm(x) for x in out[i + win:i + 2 * win]]:
                    res.extend(out[i:i + win])
                    i += 2 * win
                    while i + win <= len(out) and a == [_norm(x) for x in out[i:i + win]]:
                        i += win
                    changed = True
                else:
                    res.append(out[i])
                    i += 1
            out = res
    return " ".join(out).strip()


def _collapse_bursts(cues: list) -> list:
    """A busy end-card (stacked review badges, star ratings, CTAs) OCRs
    slightly differently every frame, yielding a run of short, jumbled,
    near-duplicate cues. Collapse any run of >=3 short, tightly packed cues
    into a single cue whose text is the fullest single observation, deduped.
    Stable subtitle cues (a title held for a second+) are left untouched."""
    out, i, n = [], 0, len(cues)
    while i < n:
        j = i
        while (j + 1 < n
               and cues[j]["end"] - cues[j]["start"] <= 1.0
               and cues[j + 1]["start"] - cues[j]["end"] <= 0.6):
            j += 1
        if j - i >= 2:  # a run of >=3 short adjacent cues -> flicker burst
            members = cues[i:j + 1]
            rep = max(members, key=lambda c: _distinct_real(c["text"]))
            out.append({"start": members[0]["start"], "end": members[-1]["end"],
                        "text": _dedup_words(rep["text"])})
        else:
            c = dict(cues[i])
            c["text"] = _dedup_words(c["text"])
            out.append(c)
        i = j + 1
    return out


def ocr_captions(source: Path, tmp_root: Path, fps: float = 2.0) -> list:
    """Read the burned-in on-screen captions over time via OCR.

    White caption text is isolated (bright-pixel threshold, inverted) so
    decorative background art doesn't drown it. Each frame is OCR'd in TSV
    mode and only words tesseract is confident about (conf >= 60) that also
    read as real words survive - this is what keeps texture/vine noise out.
    Static overlays present in most frames (reply stickers, @handles) are
    dropped, and consecutive frames with the same wording collapse into one
    time-ranged cue.
    """
    import csv
    import io
    from collections import Counter
    from concurrent.futures import ThreadPoolExecutor

    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    work = Path(tempfile.mkdtemp(dir=tmp_root))
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-vf",
         f"fps={fps},format=gray,lut=y='if(gt(val,205),0,255)',scale=iw*1.4:-1",
         f"{work}/f%05d.png"],
        check=True)
    frames = sorted(work.glob("f*.png"))

    def ocr(f):
        r = subprocess.run(["tesseract", str(f), "stdout", "--psm", "6", "tsv"],
                           capture_output=True, text=True, env=env)
        by_line = {}
        for row in csv.DictReader(io.StringIO(r.stdout), delimiter="\t"):
            try:
                conf = float(row.get("conf", "-1"))
            except ValueError:
                conf = -1
            txt = (row.get("text") or "").strip()
            if conf < 60 or not txt:
                continue
            key = (row.get("block_num"), row.get("par_num"), row.get("line_num"))
            by_line.setdefault(key, []).append(txt)
        out = []
        for words in by_line.values():
            real = [w for w in words if _is_word(w)]
            # a line must be mostly recognisable words, at least two of them
            if len(real) >= 2 and len(real) >= 0.6 * len(words):
                out.append(re.sub(r"\s+", " ", " ".join(words)).strip())
        return out

    with ThreadPoolExecutor(max_workers=4) as pool:
        per = list(pool.map(ocr, frames))

    n = max(1, len(per))
    freq = Counter()
    for lines in per:
        for k in {_line_key(l) for l in lines}:
            freq[k] += 1
    static = {k for k, c in freq.items() if c > 0.45 * n and len(k) > 3}

    cues = []
    for i, lines in enumerate(per):
        txt = re.sub(r"\s+", " ",
                     " ".join(l for l in lines if _line_key(l) not in static)).strip()
        if len(txt) < 4:
            continue
        t = i / fps
        if cues and _similar(cues[-1]["text"], txt):
            cues[-1]["end"] = round(t + 1 / fps, 2)
            if len(txt) > len(cues[-1]["text"]):
                cues[-1]["text"] = txt
        else:
            cues.append({"start": round(t, 2), "end": round(t + 1 / fps, 2),
                         "text": txt})
    cues = _collapse_bursts(cues)
    return [c for c in cues if c["end"] - c["start"] >= 0.5]


def whisper_cues(source: Path) -> list:
    """Speech-to-text fallback for narration videos with no on-screen text."""
    from faster_whisper import WhisperModel
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(source), vad_filter=True)
    return [{"start": round(s.start, 2), "end": round(s.end, 2),
             "text": s.text.strip()} for s in segments if s.text.strip()]


def write_transcript(source: Path, req: dict, tmp_root: Path = None) -> int:
    """Extract the video's captions (on-screen text first, speech as a
    fallback) and store editable cues under data/transcripts."""
    tmp_root = tmp_root or Path(tempfile.mkdtemp())
    source_kind = "ocr"
    try:
        cues = ocr_captions(source, tmp_root)
    except Exception as e:
        print(f"ocr captions failed: {e}", file=sys.stderr)
        cues = []
    if len(cues) < 3:
        try:
            speech = whisper_cues(source)
            if len(speech) > len(cues):
                cues, source_kind = speech, "speech"
        except Exception as e:
            print(f"whisper fallback failed: {e}", file=sys.stderr)
    key = transcript_key(req)
    out = REPO_ROOT / "data" / "transcripts" / f"{key}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"source": req.get("source_url") or req.get("source_path"),
         "kind": source_kind, "cues": cues},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"transcript ({source_kind}): {len(cues)} cues -> data/transcripts/{key}.json")
    return len(cues)


def transcribe_one(req: dict, tmp_root: Path) -> dict:
    work = Path(tempfile.mkdtemp(dir=tmp_root))
    source = resolve_source(req, work)
    n = write_transcript(source, req, tmp_root)
    return {"video": None, "caption": None, "caption_error": None,
            "transcript": transcript_key(req), "cue_count": n}


def burn_subtitles(source: Path, cues: list, work: Path) -> Path:
    """Burn edited cues onto the source (timestamps in source time)."""
    def ts(t: float) -> str:
        h, rem = divmod(float(t), 3600)
        m, s = divmod(rem, 60)
        return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")

    srt = work / "subs.srt"
    lines = []
    for i, c in enumerate(cues, start=1):
        lines += [str(i), f"{ts(c['start'])} --> {ts(c['end'])}",
                  str(c["text"]).strip(), ""]
    srt.write_text("\n".join(lines), encoding="utf-8")
    styled = (
        f"subtitles={srt}:force_style="
        "'FontName=Liberation Sans,Bold=1,FontSize=13,"
        "PrimaryColour=&HFFFFFF&,OutlineColour=&H50000000&,"
        "BorderStyle=1,Outline=2,Shadow=1,MarginV=42'"
    )
    out = work / "subtitled.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-vf", styled,
         "-c:v", "libx264", "-crf", "20", "-preset", "medium",
         "-c:a", "copy", str(out)],
        check=True)
    return out


def process_one(idx: int, req: dict, assets_by_id: dict, out_dir: Path, tmp_root: Path) -> dict:
    asset = assets_by_id.get(str(req.get("asset")))
    if asset is None:
        raise ValueError(f"unknown asset: {req.get('asset')!r}")
    outro_rel = req.get("outro") or ""
    if outro_rel not in asset.get("outros", []):
        raise ValueError(f"outro {outro_rel!r} is not registered for asset {asset['id']!r}")
    outro_path = REPO_ROOT / outro_rel
    if not outro_path.exists():
        raise ValueError(f"outro file missing from repo: {outro_rel}")
    intro_rel = req.get("intro") or ""
    intro_path = None
    if intro_rel:
        if intro_rel not in asset.get("intros", []):
            raise ValueError(f"intro {intro_rel!r} is not registered for asset {asset['id']!r}")
        intro_path = REPO_ROOT / intro_rel
        if not intro_path.exists():
            raise ValueError(f"intro file missing from repo: {intro_rel}")
    cut_raw = req.get("cut_seconds")
    cut = float(cut_raw) if cut_raw not in (None, "") else None
    if cut is not None and cut <= 0:
        raise ValueError("cut_seconds must be > 0 when provided")
    start = float(req.get("start_seconds") or 0)
    if start < 0:
        raise ValueError("start_seconds must be >= 0")
    if cut is not None and start >= cut:
        raise ValueError("start_seconds must be smaller than cut_seconds")

    work = Path(tempfile.mkdtemp(dir=tmp_root))
    source = resolve_source(req, work)

    cues = req.get("subtitles") or []
    if cues:
        source = burn_subtitles(source, cues, work)

    spec = ff.probe(source)
    if spec["duration"]:
        if start >= spec["duration"]:
            raise ValueError(
                f"start_seconds ({start:g}) is beyond the video length "
                f"({spec['duration']:.1f}s)")
        if cut is None or cut >= spec["duration"]:
            cut = spec["duration"]
    clip_len = cut - start if cut is not None else None

    trimmed = work / "trimmed.mp4"
    ff.encode_canonical(source, trimmed, spec, spec["has_audio"],
                        cut_seconds=clip_len, start_seconds=start)
    outro_spec = ff.probe(outro_path)
    outro_norm = work / "outro_norm.mp4"
    ff.encode_canonical(outro_path, outro_norm, spec, outro_spec["has_audio"])
    parts = [trimmed, outro_norm]
    if intro_path is not None:
        intro_spec = ff.probe(intro_path)
        intro_norm = work / "intro_norm.mp4"
        ff.encode_canonical(intro_path, intro_norm, spec, intro_spec["has_audio"])
        parts.insert(0, intro_norm)
    final_tmp = work / "final.mp4"
    ff.concat(parts, final_tmp)

    # Brand gate before anything is published or wired to the ledger: a
    # video still naming a source brand must not reach the approved tab.
    brand = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_brands.py"), str(final_tmp)],
        capture_output=True, text=True)
    if brand.returncode != 0:
        raise ValueError("brand check failed: " + brand.stdout.strip().replace("\n", " | "))

    final = out_dir / f"video_{idx:02d}.mp4"
    shutil.move(final_tmp, final)

    # A user-edited caption from the editor wins over auto-generation.
    caption, caption_error = req.get("caption") or None, None
    if not caption:
        try:
            if clip_len is None:
                clip_len = ff.probe(trimmed)["duration"] or 10
            frames = ff.extract_frames(trimmed, work / "frames", clip_len)
            caption = captions.generate_caption(frames, asset)
        except Exception as e:  # caption failure must not lose the video
            caption_error = str(e)
    return {"video": final.name, "caption": caption, "caption_error": caption_error}


def ledger_patch_entries(results: list[dict], batch_tag: str) -> list[dict]:
    """The ledger mutations this batch implies, as plain data (keyed by
    video_id) so they can be re-applied on top of the freshest ledger at
    commit time. This keeps the final approved+output state authoritative
    even if the frontend wrote an intermediate 'processing' status while the
    batch was running - see scripts/commit_results.py."""
    patch = []
    for r in results:
        vid = r["request"].get("ledger_video_id")
        if not vid or r["status"] != "done":
            continue
        if r.get("preview"):
            # Preview jobs only attach a clean playable copy; they don't
            # change the review status.
            patch.append({"video_id": vid,
                          "preview_asset": f"{batch_tag}/{r['preview']}"})
        elif r.get("video"):
            m = {"video_id": vid, "status": "approved",
                 "output_asset": f"{batch_tag}/{r['video']}"}
            if r.get("caption"):
                m["caption"] = r["caption"]
            patch.append(m)
    return patch


def apply_ledger_patch(ledger: list, patch: list[dict]) -> bool:
    """Apply video_id-keyed mutations to a ledger in place; returns True if
    anything changed. Unknown video_ids are skipped."""
    by_id = {e.get("video_id"): e for e in ledger}
    changed = False
    for m in patch:
        entry = by_id.get(m["video_id"])
        if not entry:
            continue
        for k, v in m.items():
            if k != "video_id":
                entry[k] = v
        changed = True
    return changed


def patch_ledger(results: list[dict], batch_tag: str) -> None:
    """Wire produced videos back to their curated ledger entries so the
    approved tab shows the processed file instead of the raw source."""
    path = REPO_ROOT / "data" / "ledger.json"
    if not path.exists():
        return
    ledger = json.loads(path.read_text(encoding="utf-8"))
    patch = ledger_patch_entries(results, batch_tag)
    if apply_ledger_patch(ledger, patch):
        path.write_text(json.dumps(ledger, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
        print(f"ledger: wired {len(patch)} entries to {batch_tag}")


def preview_one(idx: int, req: dict, out_dir: Path, tmp_root: Path) -> dict:
    """Fetch a clean copy of the source (no TikTok embed chrome / watermark)
    so the review UI can play it in a real media player."""
    work = Path(tempfile.mkdtemp(dir=tmp_root))
    source = resolve_source(req, work)
    key = transcript_key(req)
    dest = out_dir / f"preview_{key}.mp4"
    # Re-mux to a faststart mp4 so it streams in <video> without a full download.
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(source),
         "-c", "copy", "-movflags", "+faststart", str(dest)],
        check=False)
    if not dest.exists() or dest.stat().st_size == 0:
        shutil.copy(source, dest)
    print(f"preview: {dest.name}")
    # Transcribe in the same job so subtitles are ready the moment the user
    # opens the editor - one download serves both.
    cues = 0
    try:
        cues = write_transcript(source, req, tmp_root)
    except Exception as e:
        print(f"preview transcript failed: {e}", file=sys.stderr)
    return {"video": None, "caption": None, "caption_error": None,
            "preview": dest.name, "preview_key": key, "cue_count": cues}


def build_notes(results: list[dict], assets_by_id: dict, mock_warning: bool | None = None) -> str:
    if mock_warning is None:
        mock_warning = captions.is_mock_mode()
    lines = []
    if mock_warning and any(r.get("caption") for r in results):
        lines.append(
            "> ⚠️ הכיתובים נוצרו במצב דמה כי הסוד `ANTHROPIC_API_KEY` לא מוגדר "
            "בריפו. הוסיפו אותו (Settings → Secrets → Actions) כדי לקבל כיתוב "
            "ייחודי לפי תוכן הסרטון.\n"
        )
    lines.append("## תוצרים\n")
    for r in results:
        if r["request"].get("transcribe_only"):
            src = r["request"].get("source_url") or r["request"].get("source_path") or ""
            if r["status"] == "done":
                lines.append(f"### 🎙 תמלול — `{src}`")
                lines.append(f"{r.get('cue_count', 0)} שורות כתוביות נשמרו לעריכה\n")
            else:
                lines.append(f"### ❌ תמלול נכשל — `{src}`")
                lines.append(f"שגיאה: {r['error']}\n")
            continue
        asset = assets_by_id.get(str(r["request"].get("asset")), {})
        name = asset.get("name", r["request"].get("asset"))
        source = r["request"].get("source_url") or r["request"].get("source_path") or ""
        start = r["request"].get("start_seconds") or 0
        end = r["request"].get("cut_seconds")
        if end is None:
            cut = f"מ-{start:g} שניות עד הסוף" if start else "ללא חיתוך"
        elif start:
            cut = f"קטע {start:g}–{end:g} שניות"
        else:
            cut = f"חיתוך בשניה {end:g}"
        if r["status"] == "done":
            lines.append(f"### ✅ {name} — {cut}")
            lines.append(f"מקור: `{source}` · קובץ: **{r['video']}**\n")
            if r.get("caption"):
                lines.append("```\n" + r["caption"] + "\n```\n")
            elif r.get("caption_error"):
                lines.append(f"⚠️ יצירת הכיתוב נכשלה: {r['caption_error']}\n")
        else:
            lines.append(f"### ❌ {name} — {cut}")
            lines.append(f"מקור: `{source}`")
            lines.append(f"שגיאה: {r['error']}\n")
    lines.append("<!-- machine-readable -->")
    lines.append("```json")
    lines.append(json.dumps(results, ensure_ascii=False, indent=1))
    lines.append("```")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", help="JSON array (defaults to $REQUESTS_JSON)")
    parser.add_argument("--assets-file", default=str(REPO_ROOT / "assets.json"))
    parser.add_argument("--out", default="out")
    args = parser.parse_args()

    raw = args.requests or os.environ.get("REQUESTS_JSON") or ""
    requests = json.loads(raw)
    if not isinstance(requests, list) or not requests:
        print("no requests to process", file=sys.stderr)
        return 1

    assets = json.loads(Path(args.assets_file).read_text(encoding="utf-8"))
    assets_by_id = {str(a["id"]): a for a in assets}

    if captions.is_mock_mode():
        print("WARNING: captions run in PLACEHOLDER mode - the ANTHROPIC_API_KEY "
              "repository secret is not set", file=sys.stderr)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory() as tmp_root:
        for idx, req in enumerate(requests, start=1):
            entry = {"index": idx, "request": req, "status": "done",
                     "video": None, "caption": None, "caption_error": None, "error": None}
            try:
                if req.get("transcribe_only"):
                    entry.update(transcribe_one(req, Path(tmp_root)))
                    print(f"[{idx}/{len(requests)}] transcribed")
                elif req.get("preview_only"):
                    entry.update(preview_one(idx, req, out_dir, Path(tmp_root)))
                    print(f"[{idx}/{len(requests)}] preview ready")
                else:
                    entry.update(process_one(idx, req, assets_by_id, out_dir, Path(tmp_root)))
                    print(f"[{idx}/{len(requests)}] done -> {entry['video']}")
            except Exception as e:
                entry["status"] = "failed"
                entry["error"] = str(e)[:2000]
                print(f"[{idx}/{len(requests)}] FAILED: {e}", file=sys.stderr)
            results.append(entry)

    batch_tag = os.environ.get("BATCH_TAG")
    if batch_tag:
        patch_ledger(results, batch_tag)
        # Persist the mutations as data so the commit step can re-apply them
        # on top of the freshest main (avoids a ledger rebase race).
        (out_dir / "ledger_patch.json").write_text(
            json.dumps(ledger_patch_entries(results, batch_tag),
                       ensure_ascii=False, indent=1), encoding="utf-8")

    (out_dir / "summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "notes.md").write_text(build_notes(results, assets_by_id), encoding="utf-8")

    succeeded = sum(1 for r in results if r["status"] == "done")
    print(f"{succeeded}/{len(results)} requests succeeded")
    return 0 if succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
