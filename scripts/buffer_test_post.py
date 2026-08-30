#!/usr/bin/env python3
"""Phase-1 gate: publish ONE test post per connected platform of one
brand through Buffer, so the owner can verify the pipeline end-to-end
on the live networks before more brands migrate.

The post is a real publish (mode shareNow by default, or scheduled a
few minutes out with --in-minutes so it can still be deleted from the
Buffer dashboard). Media must be a PUBLIC URL - the default is the
repo's Pages-hosted favicon-sized logo if no --media is given, which
every platform accepts as an image post.

Usage (from .github/workflows/buffer-test.yml, or locally):
    python scripts/buffer_test_post.py <brand-id> [--media URL]
        [--video] [--in-minutes N] [--platforms instagram,facebook]
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
from generate_daily import load_json  # noqa: E402
import distribute_aimodels as run  # noqa: E402
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

    roster = load_json(run.ROSTER, [])
    char = next((c for c in roster if str(c.get("id")) == args.brand), None)
    if not char:
        print(f"::error::brand '{args.brand}' is not in roster.json",
              file=sys.stderr)
        return 1
    key = os.environ.get(run.buffer_key_env_of(char), "")
    if not key:
        print(f"::error::secret {run.buffer_key_env_of(char)} is not set",
              file=sys.stderr)
        return 1
    targets = run.buffer_targets_of(char)
    if args.platforms:
        want = {p.strip() for p in args.platforms.split(",") if p.strip()}
        targets = [t for t in targets if t["platform"] in want]
    if not targets:
        print("::error::no wired bufferChannels for this brand (run "
              "check_buffer.py first - channels auto-wire when the token "
              "works)", file=sys.stderr)
        return 1

    media = {"type": "video" if args.video else "image", "url": args.media,
             "isAi": False}
    print(f"test post for '{args.brand}' -> "
          + ", ".join(t["platform"] for t in targets))
    with run.with_buffer_key(key):
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
