#!/usr/bin/env python3
"""Update captions on existing batch releases.

Reads recaption.json:
  [{"tag": "batch-4", "captions": {"video_01.mp4": "new caption", ...}}]

For each entry: download the release's summary.json, apply the provided
captions (an entry without "captions" regenerates all of them with Claude
from video frames instead), rebuild the release notes, and push the changes
back with `gh release edit` / `gh release upload --clobber`.

Runs inside GitHub Actions with GH_TOKEN set. Use --dry-run locally to
write the rebuilt files without touching the release.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services import captions  # noqa: E402
from app.services import ffmpeg as ff  # noqa: E402
from process_batch import build_notes  # noqa: E402


def gh(*args: str) -> None:
    subprocess.run(["gh", *args], check=True)


def apply_captions(summary: list[dict], new_captions: dict) -> int:
    changed = 0
    for item in summary:
        text = new_captions.get(item.get("video") or "")
        if text:
            item["caption"] = text.strip()
            item["caption_error"] = None
            changed += 1
    return changed


def regenerate_captions(summary: list[dict], assets_by_id: dict, work: Path, tag: str) -> int:
    changed = 0
    for item in summary:
        video = item.get("video")
        if not video or item.get("status") != "done":
            continue
        asset = assets_by_id.get(str(item.get("request", {}).get("asset")))
        if asset is None:
            continue
        gh("release", "download", tag, "--pattern", video, "--dir", str(work), "--clobber")
        path = work / video
        info = ff.probe(path)
        cut = float(item.get("request", {}).get("cut_seconds") or 0)
        duration = min(cut, info["duration"]) if cut and info["duration"] else (info["duration"] or 10)
        frames = ff.extract_frames(path, work / f"frames_{video}", duration)
        item["caption"] = captions.generate_caption(frames, asset)
        item["caption_error"] = None
        changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default=str(REPO_ROOT / "recaption.json"))
    parser.add_argument("--assets-file", default=str(REPO_ROOT / "assets.json"))
    parser.add_argument("--dry-run", action="store_true",
                        help="write rebuilt notes/summary locally, skip gh calls")
    args = parser.parse_args()

    entries = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if not entries:
        print("recaption queue is empty - nothing to do")
        return 0
    assets = json.loads(Path(args.assets_file).read_text(encoding="utf-8"))
    assets_by_id = {str(a["id"]): a for a in assets}

    for entry in entries:
        tag = entry["tag"]
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            if args.dry_run:
                summary_path = REPO_ROOT / f"summary-{tag}.json"
            else:
                gh("release", "download", tag, "--pattern", "summary.json",
                   "--dir", str(work), "--clobber")
                summary_path = work / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))

            if entry.get("captions"):
                changed = apply_captions(summary, entry["captions"])
                mock_warning = False
            else:
                changed = regenerate_captions(summary, assets_by_id, work, tag)
                mock_warning = None
            print(f"{tag}: updated {changed} captions")
            if not changed:
                continue

            notes = build_notes(summary, assets_by_id, mock_warning)
            out_summary = work / "summary.json"
            out_notes = work / "notes.md"
            out_summary.write_text(
                json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
            out_notes.write_text(notes, encoding="utf-8")
            if args.dry_run:
                dst = REPO_ROOT / f"recaption-out-{tag}"
                dst.mkdir(exist_ok=True)
                (dst / "summary.json").write_text(
                    out_summary.read_text(encoding="utf-8"), encoding="utf-8")
                (dst / "notes.md").write_text(notes, encoding="utf-8")
                print(f"{tag}: dry-run output in {dst}")
            else:
                gh("release", "edit", tag, "--notes-file", str(out_notes))
                gh("release", "upload", tag, str(out_summary), "--clobber")
    return 0


if __name__ == "__main__":
    sys.exit(main())
