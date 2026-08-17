#!/usr/bin/env python3
"""One-shot backfill of already-generated AI-models images into batch.json.

data/aimodels/backlog_sources.json lists 108 images (9 characters x 12)
generated on 2026-08-14 whose source URLs live on Higgsfield's CDN and
EXPIRE around 2026-08-21. Every image is re-hosted through the shared
persist_image (permanent release asset, tag aimodels-backfill-<gen date>)
- the gateway law: batch.json may only ever hold permanent links.

Per backlog entry:
- Prefer a local copy when a Desktop AI_Models folder is available
  (--local-dir, layout <dir>/<char>/feed/*; matched to entries by sorted
  order). Otherwise download from source_url while it is still alive.
- An entry whose source_url is already the `image` of an existing batch
  item (the original seed posts) is RELINKED in place - image swapped to
  the permanent URL, generatedAt filled in - keeping its status, caption
  and schedule untouched.
- Anything else becomes a new pending item: caption in the character's
  voice via the same make_caption as the daily run, schedule spread one
  item per character per day starting tomorrow so a character's backlog
  never posts in one clump.

Idempotent: new-item ids derive from the source URL hash, so a re-run
adds nothing and relinks nothing twice.
"""
import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_daily import (  # noqa: E402
    REPO_ROOT, commit_batch, load_json, make_caption, persist_image_and_thumb,
)

# What each backlog theme depicts - feeds the caption prompt. The backlog's
# "type" column is a theme; in batch.json `type` stays "post" (the
# post/reel/story axis) and the theme is kept in its own field.
THEME_SCENES = {
    "fashion": "a candid outfit check / street-style moment in {city}",
    "food": "enjoying local food at a cozy spot in {city}",
    "lifestyle": "an aesthetic slice-of-life moment in {city}",
    "everyday": "a completely casual everyday moment at home or around the neighborhood",
    "experience": "out exploring {city} - landmarks, markets, small adventures",
    "swim": "a sunny pool or beach day near {city}",
    "friends": "hanging out with friends (others out of focus), fun casual energy",
}


def stable_id(char: str, source_url: str) -> str:
    return f"{char}-bf-{hashlib.sha1(source_url.encode()).hexdigest()[:10]}"


def find_local(local_dir: Path, char: str, index: int):
    """Best-effort local copy: <local_dir>/<char>/feed/* sorted by name,
    matched to the character's backlog entries by order. Returns None when
    the folder or the index is missing - caller falls back to the CDN."""
    for cand in (local_dir / char / "feed", local_dir / char.capitalize() / "feed"):
        if cand.is_dir():
            files = sorted(p for p in cand.iterdir() if p.is_file())
            if index < len(files):
                return files[index]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources",
                    default=str(REPO_ROOT / "data" / "aimodels" / "backlog_sources.json"))
    ap.add_argument("--local-dir", default=str(Path.home() / "Desktop" / "AI_Models"),
                    help="preferred image source when it exists (Dudi's disk)")
    ap.add_argument("--out", default="out/backfill")
    args = ap.parse_args()

    backlog = json.loads(Path(args.sources).read_text(encoding="utf-8"))["items"]
    roster = {c["id"]: c for c in
              load_json(REPO_ROOT / "data" / "aimodels" / "roster.json", [])}
    plan = load_json(REPO_ROOT / "data" / "aimodels" / "plan.json", {})
    times = plan.get("post_times_by_char", {})
    batch = load_json(REPO_ROOT / "data" / "aimodels" / "batch.json",
                      {"week": "", "posts": []})
    by_image = {p.get("image"): p for p in batch.get("posts", [])}
    existing_ids = {p.get("id") for p in batch.get("posts", [])}

    local_dir = Path(args.local_dir)
    use_local = local_dir.is_dir()
    print(f"local AI_Models dir: {'FOUND ' + str(local_dir) if use_local else 'not present - using source URLs'}")

    out_dir = Path(args.out)
    start = datetime.date.today() + datetime.timedelta(days=1)
    per_char_index: dict[str, int] = {}
    new_items, relinks, skipped, failures = [], {}, 0, 0
    from_local = from_cdn = 0

    for entry in backlog:
        char_id, theme = entry["char"], entry["type"]
        src = entry["source_url"]
        gen_date = str(entry.get("generatedAt", ""))[:10]
        tag = f"aimodels-backfill-{gen_date or 'unknown'}"
        char = roster.get(char_id)
        if not char:
            print(f"[{char_id}] not in roster - skipping", file=sys.stderr)
            failures += 1
            continue
        item_id = stable_id(char_id, src)
        idx = per_char_index.setdefault(char_id, 0)

        already = by_image.get(src)
        if item_id in existing_ids or (already and "releases/download" in str(already.get("image"))):
            skipped += 1
            continue

        source = None
        if use_local:
            source = find_local(local_dir, char_id, idx)
        if source is not None:
            from_local += 1
        else:
            source = src
            from_cdn += 1

        try:
            permanent, thumb = persist_image_and_thumb(source, tag, f"{item_id}.png", out_dir)
        except Exception as e:
            print(f"[{item_id}] persist FAILED: {e}", file=sys.stderr)
            failures += 1
            continue

        if already:
            # Original seed post - swap the expiring link for the permanent
            # copy, stamp generatedAt, change nothing else.
            relinks[src] = {"image": permanent, "generatedAt": gen_date}
            print(f"[{item_id}] relinked existing item ({already.get('id')})")
        else:
            scene = THEME_SCENES.get(theme, THEME_SCENES["everyday"]).format(
                city=char.get("city", ""))
            caption = make_caption(char, scene)
            date = (start + datetime.timedelta(days=idx)).isoformat()
            per_char_index[char_id] = idx + 1
            new_items.append({
                "id": item_id,
                "char": char_id,
                "type": "post",
                "theme": theme,
                "image": permanent,
                **({"thumb": thumb} if thumb else {}),
                "caption": caption,
                "date": date,
                "time": times.get(char_id, "19:00"),
                "status": "pending",
                "generatedAt": gen_date,
            })
            print(f"[{item_id}] new item ({theme}) scheduled {date}")

    print(f"\nbackfill summary: {len(new_items)} new, {len(relinks)} relinked, "
          f"{skipped} already done, {failures} failed "
          f"(sources: {from_local} local / {from_cdn} cdn)")
    if failures and not new_items and not relinks:
        return 1

    def mutate(b):
        ids = {p.get("id") for p in b.get("posts", [])}
        added = [i for i in new_items if i["id"] not in ids]
        changed = bool(added)
        for p in b.get("posts", []):
            r = relinks.get(p.get("image"))
            if r:
                p["image"] = r["image"]
                if not p.get("generatedAt"):
                    p["generatedAt"] = r["generatedAt"]
                changed = True
        b["posts"] = b.get("posts", []) + added
        return changed

    rc = commit_batch(
        mutate,
        f"AI models: backfill {len(new_items)} items + {len(relinks)} relinks (permanent assets)")
    return rc if rc else (1 if failures else 0)


if __name__ == "__main__":
    sys.exit(main())
