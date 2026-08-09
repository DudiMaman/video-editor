#!/usr/bin/env python3
"""OCR every frame of a video and fail if a competitor brand name appears.

Usage: check_brands.py VIDEO [--fps 2]

Deterministic backstop for the visual brand sweep: burned-in captions or
posters naming a source app ("Download Planta now!", endcard logos) must
never reach the review queue. Frames are sampled at --fps, OCR'd with
tesseract, and matched word-by-word against the brand_blocklist in
data/config.json. Any hit prints the timestamp and matched text and the
script exits 1; a clean video exits 0.

The blocklist lives in data/config.json so new source apps can be added
without touching code. Our own brand ("planty") must not be listed -
matches are whole-word, so "planta" never matches "planty".
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_BLOCKLIST = [
    "planta", "blossom", "plantin", "plantum", "picturethis",
    "picture this", "leafsnap", "plantsnap", "natureid", "nature id",
    "flora incognita", "gardn", "leafylens", "leafcheck", "herby",
    "gregsavesplants", "download greg",
]


def load_blocklist():
    try:
        cfg = json.loads((REPO_ROOT / "data" / "config.json").read_text("utf-8"))
        terms = cfg.get("brand_blocklist")
        if isinstance(terms, list) and terms:
            return [str(t).lower() for t in terms]
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return DEFAULT_BLOCKLIST


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--fps", type=float, default=2.0,
                    help="frames sampled per second (default 2)")
    args = ap.parse_args()

    patterns = [(t, re.compile(r"\b" + re.escape(t).replace(r"\ ", r"\s+") + r"\b"))
                for t in load_blocklist()]

    # One OMP thread per tesseract - four single-threaded workers beat
    # four processes fighting over the same cores.
    env = dict(os.environ, OMP_THREAD_LIMIT="1")

    def ocr(path):
        r = subprocess.run(["tesseract", str(path), "stdout", "--psm", "11"],
                           capture_output=True, text=True, env=env)
        return re.sub(r"\s+", " ", r.stdout).lower()

    def scan_frame(item):
        i, frame = item
        # Pass 1: the frame as-is (posters, dark logos). Pass 2: bright
        # pixels only, inverted to black-on-white - white burned-in
        # captions sit on busy footage and OCR misses them otherwise.
        text = ocr(frame)
        for variant in range(2):
            for term, pat in patterns:
                m = pat.search(text)
                if m:
                    ctx = text[max(0, m.start()-30):m.end()+30].strip()
                    return (i / args.fps, term, ctx)
            if variant == 0:
                white = frame.with_name(frame.stem + "_w.png")
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-i", str(frame), "-vf",
                     "format=gray,lut=y='if(gt(val,205),0,255)'", str(white)],
                    check=True)
                text = ocr(white)
        return None

    with tempfile.TemporaryDirectory() as td:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", args.video,
             "-vf", f"fps={args.fps}", f"{td}/f%05d.png"],
            check=True)
        frames = sorted(Path(td).glob("f?????.png"))
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = pool.map(scan_frame, enumerate(frames))
        hits = [h for h in results if h]

    if hits:
        print(f"BRAND CHECK FAILED: {args.video}")
        for t, term, ctx in hits:
            print(f"  t={t:.1f}s  '{term}'  ...{ctx}...")
        return 1
    print(f"brand check clean: {args.video}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
