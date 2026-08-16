#!/usr/bin/env python3
"""Universal intake for the AI-models tab - the gateway law's catch-all.

The דמויות AI tab is the project's single entry point: content that is not
in data/aimodels/batch.json does not exist. The daily Action and the
backfill already write there; this intake covers EVERY other source -
images generated ad hoc in chat, one-off experiments, future tools.

Drop items into data/aimodels/intake.json (a push triggers the workflow):

  [
    {"char": "yuna", "image": "https://.../photo.png",
     "theme": "fashion",                # optional, drives the caption
     "caption": "...",                  # optional - generated when absent
     "date": "2026-08-20", "time": "19:00",   # optional schedule
     "generatedAt": "2026-08-18"}       # optional, defaults to today
  ]

Each item's image goes through the shared persist_image (permanent
release asset, tag aimodels-intake-<today>), gets a caption in the
character's voice when none is given, and joins batch.json as a pending
item awaiting approval. The intake file is cleared in the same commit.
Idempotent via source-hash ids - re-pushing a processed item is a no-op.
"""
import datetime
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_aimodels import THEME_SCENES  # noqa: E402
from generate_daily import (  # noqa: E402
    REPO_ROOT, commit_batch, load_json, make_caption, persist_image,
)

INTAKE = REPO_ROOT / "data" / "aimodels" / "intake.json"


def main() -> int:
    items = load_json(INTAKE, [])
    if not items:
        print("intake is empty - nothing to do")
        return 0
    roster = {c["id"]: c for c in
              load_json(REPO_ROOT / "data" / "aimodels" / "roster.json", [])}
    plan = load_json(REPO_ROOT / "data" / "aimodels" / "plan.json", {})
    times = plan.get("post_times_by_char", {})
    today = datetime.date.today().isoformat()
    tag = f"aimodels-intake-{today}"
    out_dir = Path("out/intake")

    new_items, failures = [], 0
    for entry in items:
        char_id = entry.get("char")
        src = entry.get("image", "")
        char = roster.get(char_id)
        if not char or not src:
            print(f"invalid intake entry (char={char_id}): needs char+image",
                  file=sys.stderr)
            failures += 1
            continue
        item_id = f"{char_id}-in-{hashlib.sha1(src.encode()).hexdigest()[:10]}"
        theme = entry.get("theme", "everyday")
        try:
            permanent = persist_image(src, tag, f"{item_id}.png", out_dir)
        except Exception as e:
            print(f"[{item_id}] persist FAILED: {e}", file=sys.stderr)
            failures += 1
            continue
        caption = (entry.get("caption") or "").strip() or make_caption(
            char, THEME_SCENES.get(theme, THEME_SCENES["everyday"]).format(
                city=char.get("city", "")))
        new_items.append({
            "id": item_id,
            "char": char_id,
            "type": entry.get("type", "post"),
            "theme": theme,
            "image": permanent,
            "caption": caption,
            "date": entry.get("date") or today,
            "time": entry.get("time") or times.get(char_id, "19:00"),
            "status": "pending",
            "generatedAt": entry.get("generatedAt") or today,
        })
        print(f"[{item_id}] intake ok ({theme})")

    print(f"intake summary: {len(new_items)} item(s), {failures} failure(s)")
    if not new_items:
        return 1 if failures else 0

    def mutate(batch):
        ids = {p.get("id") for p in batch.get("posts", [])}
        added = [i for i in new_items if i["id"] not in ids]
        # Clear the intake queue in the same commit even when everything
        # was already processed, so the file never re-triggers work.
        INTAKE.write_text("[]\n", encoding="utf-8")
        if added:
            batch["posts"] = batch.get("posts", []) + added
        return True

    rc = commit_batch(mutate,
                      f"AI models: intake {len(new_items)} item(s)",
                      extra_paths=("data/aimodels/intake.json",))
    return rc if rc else (1 if failures else 0)


if __name__ == "__main__":
    sys.exit(main())
