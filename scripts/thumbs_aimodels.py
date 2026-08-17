#!/usr/bin/env python3
"""One-shot (idempotent) thumbnail pass over the AI-models assets.

The gallery was loading the full multi-MB originals (backfill PNGs run
7-10MB, roster castings ~5MB) into ~240px cards - the tab crawled and
images rendered half-loaded ("pixelated"). New content gets a thumb at
creation time (persist_image_and_thumb); this script retrofits everything
that predates it:

- every batch.json item without `thumb`: download its image, build a
  480px progressive JPEG, upload it NEXT TO the original (same release
  tag, <name>.thumb.jpg), record `thumb` on the item.
- every roster character without `casting_thumb`: same, into the
  aimodels-roster release; the tab's avatars and roster grid use it.

Re-runs skip everything that already has a thumb.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_daily import (  # noqa: E402
    REPO_ROOT, REPO_SLUG, commit_batch, load_json, make_thumb, persist_image,
)

PREFIX = f"https://github.com/{REPO_SLUG}/releases/download/"


def tag_and_name(url: str):
    if not url.startswith(PREFIX):
        return None, None
    parts = url[len(PREFIX):].split("/")
    return (parts[0], parts[1]) if len(parts) == 2 else (None, None)


def build_thumb(url: str, out_dir: Path) -> str | None:
    tag, name = tag_and_name(url)
    if not tag:
        print(f"  not a release asset, skipping: {url[:80]}", file=sys.stderr)
        return None
    tname = f"{name.rsplit('.', 1)[0]}.thumb.jpg"
    import urllib.request
    out_dir.mkdir(parents=True, exist_ok=True)
    src = out_dir / name
    req = urllib.request.Request(url, headers={"User-Agent": "thumbs/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        src.write_bytes(r.read())
    make_thumb(src, out_dir / tname)
    turl = persist_image(out_dir / tname, tag, tname, out_dir)
    src.unlink(missing_ok=True)  # keep the runner's disk small
    return turl


def main() -> int:
    out_dir = Path("out/thumbs")
    batch = load_json(REPO_ROOT / "data" / "aimodels" / "batch.json",
                      {"posts": []})
    roster = load_json(REPO_ROOT / "data" / "aimodels" / "roster.json", [])

    item_thumbs, fails = {}, 0
    todo = [p for p in batch.get("posts", []) if not p.get("thumb")]
    print(f"items needing thumbs: {len(todo)}")
    for i, p in enumerate(todo, 1):
        try:
            t = build_thumb(p["image"], out_dir)
            if t:
                item_thumbs[p["id"]] = t
            print(f"[{i}/{len(todo)}] {p['id']} ok")
        except Exception as e:
            fails += 1
            print(f"[{i}/{len(todo)}] {p['id']} FAILED: {e}", file=sys.stderr)

    roster_thumbs = {}
    for c in roster:
        if c.get("casting_thumb") or not c.get("casting"):
            continue
        try:
            t = build_thumb(c["casting"], out_dir)
            if t:
                roster_thumbs[c["id"]] = t
            print(f"[roster] {c['id']} ok")
        except Exception as e:
            fails += 1
            print(f"[roster] {c['id']} FAILED: {e}", file=sys.stderr)

    print(f"thumbs: {len(item_thumbs)} items + {len(roster_thumbs)} roster, "
          f"{fails} failed")
    if not item_thumbs and not roster_thumbs:
        return 1 if fails else 0

    roster_path = REPO_ROOT / "data" / "aimodels" / "roster.json"

    def mutate(b):
        changed = False
        for p in b.get("posts", []):
            t = item_thumbs.get(p.get("id"))
            if t and not p.get("thumb"):
                p["thumb"] = t
                changed = True
        if roster_thumbs:
            # roster.json rides in the same commit - re-read it fresh
            # (we are on a clean reset of origin/main inside commit_batch).
            r = load_json(roster_path, [])
            rchanged = False
            for c in r:
                t = roster_thumbs.get(c.get("id"))
                if t and not c.get("casting_thumb"):
                    c["casting_thumb"] = t
                    rchanged = True
            if rchanged:
                roster_path.write_text(
                    json.dumps(r, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
                changed = True
        return changed

    rc = commit_batch(
        mutate,
        f"AI models: gallery thumbnails ({len(item_thumbs)} items + {len(roster_thumbs)} avatars)",
        extra_paths=("data/aimodels/roster.json",))
    return rc if rc else (1 if fails else 0)


if __name__ == "__main__":
    sys.exit(main())
