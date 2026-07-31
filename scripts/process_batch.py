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
   "cut_seconds": 7.5,
   "source_url": "https://..."}              # or "source_path": "inbox/..."
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services import captions, downloader  # noqa: E402
from app.services import ffmpeg as ff  # noqa: E402


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
    cut = float(req.get("cut_seconds") or 0)
    if cut <= 0:
        raise ValueError("cut_seconds must be > 0")

    work = Path(tempfile.mkdtemp(dir=tmp_root))
    if req.get("source_url"):
        source = downloader.download(req["source_url"], work / "src")
    elif req.get("source_path"):
        source = (REPO_ROOT / req["source_path"]).resolve()
        if not source.is_relative_to(REPO_ROOT):
            raise ValueError("source_path escapes the repository")
        if not source.exists():
            raise ValueError(f"source file missing from repo: {req['source_path']}")
    else:
        raise ValueError("request needs source_url or source_path")

    spec = ff.probe(source)
    if spec["duration"] and cut >= spec["duration"]:
        cut = spec["duration"]

    trimmed = work / "trimmed.mp4"
    ff.encode_canonical(source, trimmed, spec, spec["has_audio"], cut_seconds=cut)
    outro_spec = ff.probe(outro_path)
    outro_norm = work / "outro_norm.mp4"
    ff.encode_canonical(outro_path, outro_norm, spec, outro_spec["has_audio"])
    final_tmp = work / "final.mp4"
    ff.concat([trimmed, outro_norm], final_tmp)
    final = out_dir / f"video_{idx:02d}.mp4"
    shutil.move(final_tmp, final)

    caption, caption_error = None, None
    try:
        frames = ff.extract_frames(trimmed, work / "frames", cut)
        caption = captions.generate_caption(frames, asset)
    except Exception as e:  # caption failure must not lose the video
        caption_error = str(e)
    return {"video": final.name, "caption": caption, "caption_error": caption_error}


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
        asset = assets_by_id.get(str(r["request"].get("asset")), {})
        name = asset.get("name", r["request"].get("asset"))
        source = r["request"].get("source_url") or r["request"].get("source_path") or ""
        cut = r["request"].get("cut_seconds")
        if r["status"] == "done":
            lines.append(f"### ✅ {name} — חיתוך בשניה {cut}")
            lines.append(f"מקור: `{source}` · קובץ: **{r['video']}**\n")
            if r.get("caption"):
                lines.append("```\n" + r["caption"] + "\n```\n")
            elif r.get("caption_error"):
                lines.append(f"⚠️ יצירת הכיתוב נכשלה: {r['caption_error']}\n")
        else:
            lines.append(f"### ❌ {name} — חיתוך בשניה {cut}")
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

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory() as tmp_root:
        for idx, req in enumerate(requests, start=1):
            entry = {"index": idx, "request": req, "status": "done",
                     "video": None, "caption": None, "caption_error": None, "error": None}
            try:
                entry.update(process_one(idx, req, assets_by_id, out_dir, Path(tmp_root)))
                print(f"[{idx}/{len(requests)}] done -> {entry['video']}")
            except Exception as e:
                entry["status"] = "failed"
                entry["error"] = str(e)[:2000]
                print(f"[{idx}/{len(requests)}] FAILED: {e}", file=sys.stderr)
            results.append(entry)

    (out_dir / "summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "notes.md").write_text(build_notes(results, assets_by_id), encoding="utf-8")

    succeeded = sum(1 for r in results if r["status"] == "done")
    print(f"{succeeded}/{len(results)} requests succeeded")
    return 0 if succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
