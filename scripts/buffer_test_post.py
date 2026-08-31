#!/usr/bin/env python3
"""Phase-1 gate: publish ONE test post per connected platform of one
brand through Buffer, so the owner can verify the pipeline end-to-end
on the live networks before more brands migrate.

The post is a real publish (mode shareNow by default, or scheduled a
few minutes out with --in-minutes so it can still be deleted from the
Buffer dashboard). Media must be a PUBLIC direct URL - the default is
the Pages-hosted test image, which every platform accepts; TikTok
accepts video only, so a TikTok channel is auto-skipped on an image
test (use --video --media <public mp4 URL> to test it, e.g. a
/media/apps-... mirror URL from the Pages site).

Brand lookup spans assets.json and roster.json, by id or name
(case-insensitive) - `planty` works.

Usage (from .github/workflows/buffer-test.yml, or locally):
    python scripts/buffer_test_post.py <brand> [--media URL] [--video]
        [--in-minutes N] [--platforms instagram,facebook]
        [--caption TEXT]

Safety: refuses to run when the brand has no bufferChannels wired, and
prints every created Buffer post id so a mistaken post is one
deletePost away.
"""
import argparse
import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buffer_wiring  # noqa: E402
from distributors import buffer  # noqa: E402

# Served by the Pages deploy (frontend/public/test-post.png).
DEFAULT_MEDIA = "https://dudimaman.github.io/video-editor/test-post.png"
DEFAULT_CAPTION = ("Test post from our publishing pipeline - please ignore. "
                   "פוסט בדיקה ממערכת הפרסום — נא להתעלם.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("brand")
    ap.add_argument("--media", default=DEFAULT_MEDIA)
    ap.add_argument("--video", action="store_true",
                    help="treat --media as a video URL")
    ap.add_argument("--in-minutes", type=int, default=0,
                    help="schedule N minutes out instead of publishing now")
    ap.add_argument("--platforms", default="",
                    help="comma list; default = every wired channel")
    ap.add_argument("--caption", default=DEFAULT_CAPTION)
    args = ap.parse_args()
    buffer_wiring.load_secrets_context()

    brand, path = buffer_wiring.find_brand(args.brand)
    if not brand:
        print(f"::error::brand '{args.brand}' is not in assets.json or "
              "roster.json", file=sys.stderr)
        return 1
    key = os.environ.get(buffer_wiring.key_env_of(brand), "")
    if not key:
        print(f"::error::secret {buffer_wiring.key_env_of(brand)} is not set",
              file=sys.stderr)
        return 1
    targets = buffer_wiring.targets_of(brand)
    if args.platforms:
        want = {p.strip() for p in args.platforms.split(",") if p.strip()}
        targets = [t for t in targets if t["platform"] in want]
    if not args.video:
        skipped = [t["platform"] for t in targets if t["platform"] == "tiktok"]
        targets = [t for t in targets if t["platform"] != "tiktok"]
        if skipped:
            print("tiktok skipped on an image test (video-only platform) - "
                  "test it with --video --media <public mp4 URL>")
    if not targets:
        print("::error::no wired bufferChannels to post to (run the doctor "
              "first - channels auto-wire when the token works)",
              file=sys.stderr)
        return 1

    media = {"type": "video" if args.video else "image", "url": args.media,
             "isAi": False}
    print(f"test post for '{brand.get('id')}' ({path.name}) -> "
          + ", ".join(t["platform"] for t in targets))
    with buffer_wiring.with_key(key):
        if args.in_minutes > 0:
            when = (datetime.datetime.now(datetime.timezone.utc)
                    + datetime.timedelta(minutes=args.in_minutes)
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
            res = buffer.schedule_post(targets, media, args.caption, when)
            print(f"scheduled for {when}")
        else:
            res = buffer.publish_now(targets, media, args.caption)
    print(f"created Buffer post id(s): {res['externalId']}")
    print(f"status: {res['status']}")
    for p, u in (res.get("urls") or {}).items():
        print(f"  live on {p}: {u}")
    for e in res.get("errors") or []:
        print(f"::warning::{e['platform']}: {e['message']}")
    print("check each platform, then approve extending the migration.")
    return 0 if not res.get("errors") else 1


if __name__ == "__main__":
    sys.exit(main())
