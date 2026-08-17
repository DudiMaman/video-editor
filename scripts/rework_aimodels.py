#!/usr/bin/env python3
"""Rework loop for the AI-models tab: fix a good image instead of
rejecting it.

When Dudi hits "שלח לעיבוד" on an item and describes the flaw in plain
words, the tab marks the item status "reworking" with rework.reason and
saves batch.json - which triggers this workflow. For every such item:

1. The plain-language complaint is translated (Claude API) into precise
   English edit instructions for the image engine.
2. The image is EDITED - not regenerated - with Higgsfield nano-banana
   (identity-preserving image editing) using the current image plus the
   character sheet as inputs. nano-banana requires image-content-type
   URLs, so when the release-asset URL is rejected a byte-exact copy is
   pushed to the aimodels-assets branch and served via
   raw.githubusercontent (real image/png). Falls back to soul/reference
   regeneration (original prompt + fix notes) if editing fails.
3. The result goes through persist_image_and_thumb (permanence law) and
   the item is updated in place: previous image kept as prev_image /
   prev_thumb for the compare view, status restored to what it was
   before the rework, rework.done stamped - the tab shows
   "חזרה מעיבוד מחדש".

Idempotent: only items with status "reworking" and no rework.done are
processed; the loop's own commit re-triggers the workflow, which then
finds nothing to do and exits.
"""
import base64
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_daily import (  # noqa: E402
    CAPTION_MODEL, REPO_ROOT, REPO_SLUG, USER_AGENT, commit_batch, load_json,
    persist_image_and_thumb, reference_candidates, submit_and_poll,
)

ASSETS_BRANCH = "aimodels-assets"


def gh_api(*args):
    r = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    return json.loads(r.stdout) if r.stdout.strip() else {}


def raw_hosted_copy(url: str, name: str) -> str:
    """Host a byte-exact copy of `url` on the aimodels-assets branch and
    return its raw.githubusercontent URL, which serves a real image
    content-type (release assets are application/octet-stream, which the
    engine rejects). The request body rides in a --input file: a multi-MB
    image as a base64 argv element dies on the kernel's 128KB
    per-argument cap ("Argument list too long")."""
    import tempfile
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    try:
        gh_api(f"repos/{REPO_SLUG}/git/ref/heads/{ASSETS_BRANCH}")
    except Exception:
        main_sha = gh_api(f"repos/{REPO_SLUG}/git/ref/heads/main")["object"]["sha"]
        gh_api(f"repos/{REPO_SLUG}/git/refs", "-f", f"ref=refs/heads/{ASSETS_BRANCH}",
               "-f", f"sha={main_sha}")
    path = f"rework/{name}"
    payload = {"message": f"Rework input copy {name}",
               "branch": ASSETS_BRANCH,
               "content": base64.b64encode(data).decode()}
    body = Path(tempfile.mkstemp(suffix=".putbody.json")[1])

    def put():
        body.write_text(json.dumps(payload))
        gh_api(f"repos/{REPO_SLUG}/contents/{path}", "-X", "PUT",
               "--input", str(body))
    try:
        put()
    except Exception as e:
        if "sha" not in str(e):
            raise
        cur = gh_api(f"repos/{REPO_SLUG}/contents/{path}?ref={ASSETS_BRANCH}")
        payload["sha"] = cur["sha"]
        put()
    body.unlink(missing_ok=True)
    return f"https://raw.githubusercontent.com/{REPO_SLUG}/{ASSETS_BRANCH}/{path}"


def refine_notes(reason: str, item: dict, char: dict) -> str:
    """Dudi's plain-language complaint -> precise engine instructions."""
    guard = ("Keep everything else identical: same person, same face, same "
             "pose, same outfit, same background and lighting.")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return f"{reason}. {guard}"
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=CAPTION_MODEL, max_tokens=300,
            messages=[{"role": "user", "content":
                "Translate the owner's plain feedback about a generated "
                "influencer photo into precise editing instructions for an "
                "image-editing model. Feedback (Hebrew or English): "
                f"\"{reason}\". Photo context: {item.get('theme', 'lifestyle')} "
                f"photo of {char.get('name', item.get('char'))} in "
                f"{char.get('city', '')}. Return ONLY 1-3 short imperative "
                "English instructions, comma-separated, describing exactly "
                "what to fix - nothing else."}])
        text = " ".join(b.text for b in msg.content if b.type == "text").strip()
        text = re.sub(r"\s+", " ", text)
        return f"{text}. {guard}" if text else f"{reason}. {guard}"
    except Exception as e:
        print(f"  notes refinement fallback ({e})", file=sys.stderr)
        return f"{reason}. {guard}"


def edit_image(item: dict, char: dict, notes: str) -> str:
    """nano-banana edit with a content-type-safe input chain; falls back
    to soul/reference regeneration (original prompt + fix notes)."""
    item_id = item["id"]
    candidates = [item["image"]]
    for src in candidates:
        try:
            return submit_and_poll("/nano-banana", {
                "prompt": notes,
                "input_images": [{"type": "image_url", "image_url": src}],
                "aspect_ratio": "3:4",
                "output_format": "jpeg",
            })
        except RuntimeError as e:
            if "invalid_image_url" not in str(e):
                raise
            print(f"  [{item_id}] engine rejected {src.split('/')[2]}, "
                  "hosting a raw copy", file=sys.stderr)
    raw = raw_hosted_copy(item["image"], f"{item_id}-in.png")
    try:
        return submit_and_poll("/nano-banana", {
            "prompt": notes,
            "input_images": [{"type": "image_url", "image_url": raw}],
            "aspect_ratio": "3:4",
            "output_format": "jpeg",
        })
    except RuntimeError as e:
        print(f"  [{item_id}] nano-banana failed ({str(e)[:120]}), "
              "regenerating via soul/reference", file=sys.stderr)
        prompt = (item.get("prompt") or "") + " " + notes
        for ref in reference_candidates(char):
            try:
                return submit_and_poll("/higgsfield-ai/soul/reference", {
                    "prompt": prompt.strip(),
                    "image_reference_url": ref,
                    "aspect_ratio": "3:4",
                    "resolution": "1080p",
                    "enhance_prompt": False,
                    "batch_size": 1,
                })
            except RuntimeError as e2:
                if "invalid_image_url" in str(e2):
                    continue
                raise
        raise RuntimeError("all engines rejected the rework")


def main() -> int:
    batch = load_json(REPO_ROOT / "data" / "aimodels" / "batch.json", {"posts": []})
    roster = {c["id"]: c for c in
              load_json(REPO_ROOT / "data" / "aimodels" / "roster.json", [])}
    todo = [p for p in batch.get("posts", [])
            if p.get("status") == "reworking"
            and (p.get("rework") or {}).get("reason")
            and not (p.get("rework") or {}).get("done")]
    if not todo:
        print("no items waiting for rework")
        return 0
    if not os.environ.get("HIGGSFIELD_API_KEY"):
        print("::error::HIGGSFIELD_API_KEY is not set - cannot rework", file=sys.stderr)
        return 1

    out_dir = Path("out/rework")
    today = datetime.date.today().isoformat()
    tag = f"aimodels-rework-{today}"
    results, failures = {}, 0
    for p in todo:
        char = roster.get(p.get("char"), {})
        reason = p["rework"]["reason"]
        print(f"[{p['id']}] rework: {reason[:80]}")
        try:
            notes = refine_notes(reason, p, char)
            print(f"  notes: {notes[:120]}")
            rev = int((p.get("rework") or {}).get("revision") or 0) + 1
            url = edit_image(p, char, notes)
            image, thumb = persist_image_and_thumb(
                url, tag, f"{p['id']}-r{rev}.jpg", out_dir)
            results[p["id"]] = {"image": image, "thumb": thumb,
                                "notes": notes, "revision": rev}
            print(f"  done -> {p['id']}-r{rev}.jpg")
        except Exception as e:
            failures += 1
            print(f"  FAILED: {e}", file=sys.stderr)

    print(f"rework summary: {len(results)} done, {failures} failed")
    if not results:
        return 1 if failures else 0

    def mutate(b):
        changed = False
        for p in b.get("posts", []):
            r = results.get(p.get("id"))
            if not r or (p.get("rework") or {}).get("done"):
                continue
            rw = dict(p.get("rework") or {})
            p["prev_image"] = p.get("image")
            if p.get("thumb"):
                p["prev_thumb"] = p["thumb"]
            p["image"] = r["image"]
            if r["thumb"]:
                p["thumb"] = r["thumb"]
            p["status"] = rw.get("prev_status") or "pending"
            rw.update({"done": True, "completedAt": today,
                       "notes": r["notes"], "revision": r["revision"]})
            p["rework"] = rw
            changed = True
        return changed

    return commit_batch(mutate,
                        f"AI models: rework returned {len(results)} item(s)")


if __name__ == "__main__":
    sys.exit(main())
