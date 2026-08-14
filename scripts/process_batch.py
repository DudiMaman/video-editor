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


def write_transcript(source: Path, req: dict) -> int:
    """Speech-to-text `source`, store editable cues under data/transcripts."""
    from faster_whisper import WhisperModel

    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(source), vad_filter=True)
    cues = [{"start": round(s.start, 2), "end": round(s.end, 2),
             "text": s.text.strip()} for s in segments if s.text.strip()]
    key = transcript_key(req)
    out = REPO_ROOT / "data" / "transcripts" / f"{key}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"source": req.get("source_url") or req.get("source_path"),
         "language": info.language, "cues": cues},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"transcript: {len(cues)} cues -> data/transcripts/{key}.json")
    return len(cues)


def transcribe_one(req: dict, tmp_root: Path) -> dict:
    work = Path(tempfile.mkdtemp(dir=tmp_root))
    source = resolve_source(req, work)
    n = write_transcript(source, req)
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


def patch_ledger(results: list[dict], batch_tag: str) -> None:
    """Wire produced videos back to their curated ledger entries so the
    approved tab shows the processed file instead of the raw source."""
    path = REPO_ROOT / "data" / "ledger.json"
    if not path.exists():
        return
    ledger = json.loads(path.read_text(encoding="utf-8"))
    by_id = {e.get("video_id"): e for e in ledger}
    changed = False
    for r in results:
        vid = r["request"].get("ledger_video_id")
        if not vid or r["status"] != "done":
            continue
        entry = by_id.get(vid)
        if not entry:
            continue
        if r.get("preview"):
            # Preview jobs only attach a clean playable copy; they don't
            # change the review status.
            entry["preview_asset"] = f"{batch_tag}/{r['preview']}"
        else:
            entry["output_asset"] = f"{batch_tag}/{r['video']}"
            entry["status"] = "approved"
            if r.get("caption"):
                entry["caption"] = r["caption"]
        changed = True
    if changed:
        path.write_text(json.dumps(ledger, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
        print(f"ledger: wired {sum(1 for r in results if r['request'].get('ledger_video_id') and r['status']=='done')} entries to {batch_tag}")


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
        cues = write_transcript(source, req)
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

    (out_dir / "summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "notes.md").write_text(build_notes(results, assets_by_id), encoding="utf-8")

    succeeded = sum(1 for r in results if r["status"] == "done")
    print(f"{succeeded}/{len(results)} requests succeeded")
    return 0 if succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
