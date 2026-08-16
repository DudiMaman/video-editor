#!/usr/bin/env python3
"""Daily content generator for the AI-models ("דמויות AI") project.

Every morning this produces one fresh pending post per roster character:
an identity-locked image generated from the character's locked sheet via
the Higgsfield REST API (soul/reference: prompt + image_reference_url),
plus an English caption in the character's voice via the Claude API.
Items land in data/aimodels/batch.json with status "pending" - a human
approves or rejects them in the "דמויות AI" tab; nothing publishes itself.

Two phases (the workflow runs them as separate steps):

  generate (default)  Read roster+plan, skip characters that already have
                      today's item (idempotent), generate the missing
                      images into --out and describe them in
                      <out>/new_items.json. Image URLs point at the
                      release assets the workflow publishes next, because
                      Higgsfield keeps generation outputs for only ~7
                      days and the scheduler CSV needs permanent links.

  --commit            Append <out>/new_items.json to a *fresh* checkout of
                      batch.json and push - reset-and-reapply with
                      retries, same race-safe pattern as
                      scripts/commit_results.py, so a concurrent
                      "save decisions" from the tab can never strand the
                      batch.

Secrets: HIGGSFIELD_API_KEY as "key_id:key_secret" (repo secret; used as
`Authorization: Key <value>`), ANTHROPIC_API_KEY for captions (already a
repo secret; caption falls back to a simple template without it).

Reels and stories are planned in data/aimodels/plan.json but disabled
(enabled: false) until switched on deliberately - video generation is an
order of magnitude more expensive than stills.
"""
import argparse
import datetime
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_BASE = "https://platform.higgsfield.ai"
REPO_SLUG = "DudiMaman/video-editor"
CAPTION_MODEL = os.environ.get("CLAUDE_MODEL") or "claude-sonnet-5"
# Cloudflare in front of the Higgsfield API rejects urllib's default
# User-Agent with "error code: 1010" (bot-signature ban) before the request
# ever reaches the application - any descriptive UA passes.
USER_AGENT = "video-editor-daily/1.0 (+https://github.com/DudiMaman/video-editor)"

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Scene banks per theme. {city} is filled from the roster. Multiple
# variants per theme keep a character's feed from repeating itself; the
# pick is seeded per character+date so re-runs are deterministic.
SCENES = {
    "fashion": [
        "mirror selfie outfit check in a bright apartment bedroom in {city}, casual chic outfit, phone partly covering her face",
        "candid street-style shot mid-stride on a stylish {city} street, looking away from the camera",
        "trying on a jacket in a small boutique in {city}, caught mid-laugh in the fitting mirror",
    ],
    "food": [
        "sitting at a tiny local cafe in {city} about to eat a popular local dish, warm cozy light, elbows on the table",
        "holding a takeaway coffee and a pastry on a bench in {city}, morning light",
        "sharing a table of local food with friends out of frame in {city}, reaching for a plate",
    ],
    "lifestyle": [
        "golden hour on a rooftop overlooking {city}, leaning on the railing, hair moving in the breeze",
        "reading a paperback by a big window with {city} visible outside, tea steaming next to her",
        "evening walk along a lit street in {city}, soft bokeh lights behind her",
    ],
    "everyday": [
        "at home on the couch in an oversized hoodie holding a mug, lazy morning vibe, blanket half falling off",
        "grocery shopping in a neighborhood store in {city}, holding a basket, completely casual clothes",
        "walking home at dusk in {city} carrying a tote bag, tired but content",
        "doing her hair in the bathroom mirror, towel on shoulders, everyday morning routine",
    ],
    "experience": [
        "visiting a famous landmark in {city}, candid tourist moment but effortless, slightly windblown",
        "at a small weekend market in {city}, inspecting handmade goods on a stall",
        "on public transport in {city} looking out the window, city reflections on the glass",
    ],
    "swim": [
        "poolside near {city} in modest tasteful swimwear, sitting on the edge with feet in the water, sun-lit",
        "walking out of the water at a beach near {city}, modest one-piece swimsuit, squinting at the sun",
    ],
}

# The identity lock + realism guards proven in the existing batch - every
# prompt starts and ends with these regardless of scene.
IDENTITY_PREFIX = (
    "Candid iPhone-style photo of the exact same young woman as in the "
    "reference image - identical face, same person, full identity preserved. "
)
REALISM_SUFFIX = (
    " Natural light, matte skin with real texture and visible pores, subtle "
    "film grain, slightly imperfect casual framing, authentic everyday "
    "social-media photo, no beauty filter, no gloss, no plastic AI look."
)


def load_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return fallback


def api(path: str, payload: dict | None = None) -> dict:
    key = os.environ.get("HIGGSFIELD_API_KEY", "")
    req = urllib.request.Request(
        path if path.startswith("http") else API_BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Key {key}",
                 "Content-Type": "application/json",
                 "User-Agent": USER_AGENT},
        method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        # Surface the API's own error message - "Invalid credentials" vs
        # "insufficient balance" etc. need different fixes by the owner.
        body = ""
        try:
            body = e.read().decode()[:300]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code} from Higgsfield: {body or e.reason}") from None


def generate_image(reference_url: str, prompt: str) -> str:
    """The one swappable engine function: identity-preserving generation
    from a reference URL, returns the produced image URL.

    Engine: Higgsfield soul/reference (see module docstring). To swap
    providers later, only this function changes.
    """
    sub = api("/higgsfield-ai/soul/reference", {
        "prompt": prompt,
        "image_reference_url": reference_url,
        "aspect_ratio": "3:4",       # closest supported to the tab's 4:5 crop
        "resolution": "1080p",
        "enhance_prompt": False,     # our prompts carry the realism language
        "batch_size": 1,
    })
    status_url = sub.get("status_url") or f"{API_BASE}/requests/{sub['request_id']}/status"
    deadline = time.time() + 360
    while time.time() < deadline:
        st = api(status_url)
        if st.get("status") == "completed":
            images = st.get("images") or []
            if not images:
                raise RuntimeError("completed without images")
            return images[0].get("url")
        if st.get("status") in ("failed", "nsfw", "canceled"):
            raise RuntimeError(f"generation {st.get('status')}: {st.get('error')}")
        time.sleep(5)
    raise RuntimeError("generation timed out after 6 minutes")


def make_caption(char: dict, scene: str) -> str:
    """English caption in the character's voice via the Claude API; falls
    back to a plain template when the key is missing so a caption problem
    never kills the image run."""
    fallback = f"today in {char['city']} 🖤 #{char['city'].lower().replace(' ', '')} #dailylife #ootd"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return fallback
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=CAPTION_MODEL, max_tokens=200,
            messages=[{"role": "user", "content":
                f"You write Instagram captions for {char['name']}, a "
                f"{char['city']}-based lifestyle creator. Voice: casual, "
                "playful, first person, lowercase-leaning, one or two emoji. "
                f"The photo shows: {scene}. Write ONE caption, max 20 words, "
                "then 3-5 fitting hashtags. English only. Return only the "
                "caption text."}])
        text = " ".join(b.text for b in msg.content if b.type == "text").strip()
        return re.sub(r"\s+", " ", text) or fallback
    except Exception as e:
        print(f"  caption fallback ({e})", file=sys.stderr)
        return fallback


def pick_theme(rng: random.Random, plan: dict) -> str:
    if rng.random() < plan.get("everyday_ratio", 0.4):
        return "everyday"
    other = [t for t in plan.get("themes", list(SCENES)) if t != "everyday" and t in SCENES]
    return rng.choice(other) if other else "everyday"


def planned_types(plan: dict, weekday: str) -> list[str]:
    types = ["post"] * int(plan.get("daily_posts_per_character", 1))
    for t in ("reels", "stories"):
        cfg = plan.get(t) or {}
        if cfg.get("enabled") and weekday in cfg.get("days", []):
            types += [t.rstrip("s")] * int(cfg.get("per_character", 1))
    return types


def phase_generate(out_dir: Path) -> int:
    roster = load_json(REPO_ROOT / "data" / "aimodels" / "roster.json", [])
    plan = load_json(REPO_ROOT / "data" / "aimodels" / "plan.json", {})
    batch = load_json(REPO_ROOT / "data" / "aimodels" / "batch.json",
                      {"week": "", "posts": []})
    if not roster:
        print("roster is empty - nothing to do", file=sys.stderr)
        return 1
    if not os.environ.get("HIGGSFIELD_API_KEY"):
        print("::error::HIGGSFIELD_API_KEY secret is not set. Create an API "
              "key at cloud.higgsfield.ai and save it as a repository secret "
              "named HIGGSFIELD_API_KEY in the form key_id:key_secret "
              "(Settings > Secrets and variables > Actions).", file=sys.stderr)
        return 1

    today = datetime.date.today().isoformat()
    weekday = WEEKDAYS[datetime.date.today().weekday()]
    types = planned_types(plan, weekday)

    # Idempotency: a (char, type) that already has an item generated today
    # is done - a re-run only fills the gaps (e.g. after a partial failure).
    have = {(p.get("char"), p.get("type", "post"))
            for p in batch.get("posts", [])
            if str(p.get("generatedAt", ""))[:10] == today}

    out_dir.mkdir(parents=True, exist_ok=True)
    release_tag = f"aimodels-{today}"
    new_items, failures = [], 0
    for char in roster:
        for typ in types:
            if (char["id"], typ) in have:
                print(f"[{char['id']}/{typ}] already generated today - skip")
                continue
            rng = random.Random(f"{today}:{char['id']}:{typ}")
            theme = pick_theme(rng, plan)
            scene = rng.choice(SCENES[theme]).format(city=char["city"])
            prompt = IDENTITY_PREFIX + scene + REALISM_SUFFIX
            item_id = f"{char['id']}-{today}-{typ}"
            try:
                print(f"[{char['id']}/{typ}] {theme}: generating...")
                url = generate_image(char["sheet"], prompt)
                dest = out_dir / f"{item_id}.jpg"
                dl = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(dl, timeout=120) as r:
                    dest.write_bytes(r.read())
                caption = make_caption(char, scene)
                new_items.append({
                    "id": item_id,
                    "char": char["id"],
                    "type": typ,
                    "image": f"https://github.com/{REPO_SLUG}/releases/download/{release_tag}/{item_id}.jpg",
                    "caption": caption,
                    "date": today,
                    "time": (plan.get("post_times_by_char") or {}).get(char["id"], "19:00"),
                    "status": "pending",
                    "generatedAt": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    "prompt": prompt,
                })
                print(f"[{char['id']}/{typ}] done -> {dest.name}")
            except Exception as e:  # one character failing must not stop the rest
                failures += 1
                print(f"[{char['id']}/{typ}] FAILED: {e}", file=sys.stderr)

    (out_dir / "new_items.json").write_text(
        json.dumps(new_items, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"generated {len(new_items)} item(s), {failures} failure(s)")
    if not new_items and failures:
        return 1  # nothing produced and something broke - fail loudly
    return 0


def git(*args, check=True):
    return subprocess.run(["git", *args], cwd=REPO_ROOT, check=check,
                          capture_output=True, text=True)


def phase_commit(out_dir: Path) -> int:
    items_file = out_dir / "new_items.json"
    new_items = json.loads(items_file.read_text()) if items_file.exists() else []
    if not new_items:
        print("no new items to commit")
        return 0

    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com")
    path = REPO_ROOT / "data" / "aimodels" / "batch.json"
    week = datetime.date.today().isocalendar()
    for attempt in range(1, 6):
        git("fetch", "origin", "main")
        git("reset", "--hard", "origin/main")
        batch = load_json(path, {"week": "", "posts": []})
        existing = {p.get("id") for p in batch.get("posts", [])}
        added = [i for i in new_items if i["id"] not in existing]
        if not added:
            print("all items already in batch.json")
            return 0
        batch["posts"] = batch.get("posts", []) + added
        batch["week"] = f"{week[0]}-W{week[1]:02d}"
        path.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        git("add", "data/aimodels/batch.json")
        git("commit", "-m", f"AI models: daily batch {datetime.date.today().isoformat()} ({len(added)} items)")
        push = git("push", "origin", "main", check=False)
        if push.returncode == 0:
            print(f"pushed {len(added)} item(s) on attempt {attempt}")
            return 0
        print(f"push rejected (attempt {attempt}): {(push.stderr or '').strip()[:200]}")
        time.sleep(2 * attempt)
    print("ERROR: could not push batch.json after 5 attempts", file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/aimodels")
    ap.add_argument("--commit", action="store_true",
                    help="append <out>/new_items.json to batch.json and push")
    args = ap.parse_args()
    out_dir = Path(args.out)
    return phase_commit(out_dir) if args.commit else phase_generate(out_dir)


if __name__ == "__main__":
    sys.exit(main())
