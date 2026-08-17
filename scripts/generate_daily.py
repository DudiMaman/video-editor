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
        "lying on a towel by a pool near {city} propped on her elbows, modest swimwear, sunglasses pushed up, soft sun flare",
    ],
}

# The identity lock + realism guards proven in the existing batch - every
# prompt starts and ends with these regardless of scene.
IDENTITY_PREFIX = (
    "Candid iPhone-style photo of the exact same young woman as in the "
    "reference image - identical face, same person, full identity preserved. "
    # The reference is a two-view studio sheet; without this guard the
    # engine sometimes reproduces the sheet itself (duplicated figure,
    # white backdrop, same outfit) instead of the requested scene - the
    # 2026-08-17 reel failed exactly this way.
    "Copy only her identity from the reference - never its white studio "
    "backdrop, its pose layout, or its outfit. Exactly one person in the "
    "frame. "
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


def persist_image(source, tag: str, name: str, work_dir: Path) -> str:
    """The permanence law: every image referenced by batch.json must live at
    a URL this repo owns that never expires. Takes a local file path or an
    http(s) URL, uploads the bytes as an asset of release <tag>, and returns
    the permanent download URL. Both the daily generation and the backfill
    go through this one function. Requires the gh CLI with GH_TOKEN (as in
    GitHub Actions runners)."""
    work_dir.mkdir(parents=True, exist_ok=True)
    local = work_dir / name
    src = str(source)
    if src.startswith("http"):
        req = urllib.request.Request(src, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=120) as r:
            local.write_bytes(r.read())
    elif Path(src).resolve() != local.resolve():
        local.write_bytes(Path(src).read_bytes())
    create = subprocess.run(
        ["gh", "release", "create", tag, str(local), "--title", tag,
         "--notes", "AI-models image assets (permanent)"],
        capture_output=True, text=True)
    if create.returncode != 0:
        up = subprocess.run(
            ["gh", "release", "upload", tag, str(local), "--clobber"],
            capture_output=True, text=True)
        if up.returncode != 0:
            raise RuntimeError(
                f"release upload failed for {name}: "
                f"{(up.stderr or create.stderr or '').strip()[:300]}")
    return f"https://github.com/{REPO_SLUG}/releases/download/{tag}/{name}"


def make_thumb(src: Path, dest: Path, width: int = 480, quality: int = 80) -> Path:
    """Small progressive-JPEG derivative for the gallery. The originals
    are multi-MB PNGs that made the tab crawl and render half-loaded
    ("pixelated"); cards only need ~480px."""
    from PIL import Image
    im = Image.open(src).convert("RGB")
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)),
                       Image.LANCZOS)
    im.save(dest, "JPEG", quality=quality, optimize=True, progressive=True)
    return dest


def persist_image_and_thumb(source, tag: str, name: str, work_dir: Path):
    """persist_image plus a companion <name>.thumb.jpg asset; returns
    (image_url, thumb_url). A failed thumbnail never fails the item -
    the UI falls back to the original."""
    url = persist_image(source, tag, name, work_dir)
    tname = f"{name.rsplit('.', 1)[0]}.thumb.jpg"
    try:
        make_thumb(work_dir / name, work_dir / tname)
        turl = persist_image(work_dir / tname, tag, tname, work_dir)
    except Exception as e:
        print(f"thumb failed for {name}: {e}", file=sys.stderr)
        turl = None
    return url, turl


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


def submit_and_poll(path: str, payload: dict, timeout: int = 360) -> str:
    """Submit a Higgsfield generation request and poll until an image URL
    is ready. Shared by the daily generator and the rework loop."""
    sub = api(path, payload)
    status_url = sub.get("status_url") or f"{API_BASE}/requests/{sub['request_id']}/status"
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = api(status_url)
        if st.get("status") == "completed":
            images = st.get("images") or []
            if images:
                return images[0].get("url")
            video = st.get("video") or {}
            if video.get("url"):
                return video["url"]
            raise RuntimeError("completed without media")
        if st.get("status") in ("failed", "nsfw", "canceled"):
            raise RuntimeError(f"generation {st.get('status')}: {st.get('error')}")
        time.sleep(5)
    raise RuntimeError(f"generation timed out after {timeout}s")


def generate_image(reference_url: str, prompt: str, aspect: str = "3:4") -> str:
    """The one swappable engine function: identity-preserving generation
    from a reference URL, returns the produced image URL.

    Engine: Higgsfield soul/reference (see module docstring). To swap
    providers later, only this function changes.
    """
    return submit_and_poll("/higgsfield-ai/soul/reference", {
        "prompt": prompt,
        "image_reference_url": reference_url,
        "aspect_ratio": aspect,      # posts: 3:4 (closest to the tab's 4:5 crop)
        "resolution": "1080p",
        "enhance_prompt": False,     # our prompts carry the realism language
        "batch_size": 1,
    })


def generate_with_refs(char: dict, prompt: str, aspect: str = "3:4",
                       kind: str = "sheet") -> str:
    """generate_image over the reference-candidate chain: a reference the
    API can't ingest (bad content-type, dead link) is not a generation
    failure - the next copy of the same reference is tried."""
    candidates = reference_candidates(char, kind)
    for ci, ref in enumerate(candidates):
        try:
            return generate_image(ref, prompt, aspect)
        except RuntimeError as e:
            if "invalid_image_url" in str(e) and ci + 1 < len(candidates):
                print(f"[{char['id']}] reference rejected "
                      f"({ref.split('/')[2]}), trying next", file=sys.stderr)
                continue
            raise
    raise RuntimeError("no usable reference")


LESSONS_PATH = REPO_ROOT / "data" / "aimodels" / "lessons.json"


def collect_rejections(batch: dict) -> list[dict]:
    """Every rejection with a written reason is a training signal."""
    out = []
    for p in batch.get("posts", []):
        r = (p.get("reject_reason") or "").strip()
        if p.get("status") == "rejected" and r:
            out.append({"char": p.get("char"), "theme": p.get("theme"),
                        "reason": r})
    return out


def distill_lessons(batch: dict) -> dict:
    """The system's learning loop: the reasons Dudi writes when rejecting
    images are distilled (via the Claude API) into short DO-NOT guidelines
    that are injected into every future generation prompt. Cached by a
    signature of the rejection set, so distillation reruns only when new
    reasoned rejections appear."""
    import hashlib
    rejections = collect_rejections(batch)
    current = load_json(LESSONS_PATH, {})
    sig = hashlib.sha1(json.dumps(rejections, sort_keys=True,
                                  ensure_ascii=False).encode()).hexdigest()
    if current.get("signature") == sig:
        return current
    if not rejections:
        return {"signature": sig, "global": [], "per_char": {}}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("no ANTHROPIC_API_KEY - keeping previous lessons", file=sys.stderr)
        return current
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=CAPTION_MODEL, max_tokens=800,
            messages=[{"role": "user", "content":
                "You maintain generation guidelines for an AI-influencer "
                "photo pipeline. Below are the owner's rejection reasons for "
                "generated photos (Hebrew or English), each with the "
                "character and theme. Distill them into concise English "
                "DO-NOT guidelines to inject into the image-generation "
                "prompt. Return ONLY JSON shaped "
                '{"global": ["..."], "per_char": {"<char>": ["..."]}}. '
                "A rule goes under per_char only when the reasons are "
                "clearly about that character; otherwise global. Max 6 "
                "global rules and 3 per character, each under 12 words, "
                "phrased as things to avoid.\n\n"
                + json.dumps(rejections, ensure_ascii=False)}])
        text = " ".join(b.text for b in msg.content if b.type == "text")
        data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        lessons = {
            "signature": sig,
            "updated": datetime.date.today().isoformat(),
            "rejections_learned_from": len(rejections),
            "global": [str(r) for r in (data.get("global") or [])][:6],
            "per_char": {k: [str(r) for r in v][:3]
                         for k, v in (data.get("per_char") or {}).items()},
        }
        print(f"lessons distilled from {len(rejections)} rejection(s): "
              f"{len(lessons['global'])} global rules")
        return lessons
    except Exception as e:
        print(f"lesson distillation failed: {e}", file=sys.stderr)
        return current


def avoid_clause(lessons: dict, char_id: str) -> str:
    """The distilled lessons, as a prompt suffix for this character."""
    rules = list(lessons.get("global") or []) + \
        list((lessons.get("per_char") or {}).get(char_id) or [])
    rules = [r.strip().rstrip(".") for r in rules if r and r.strip()][:8]
    return (" Strictly avoid: " + "; ".join(rules) + ".") if rules else ""


# Motion language per theme for reels - what the camera and the subject do
# in the 5 seconds. Subtle, handheld, nothing theatrical. The camera NEVER
# moves toward the face: the 2026-08-17 reel's push-in made the i2v model
# repaint the face at close range and the identity drifted.
REEL_MOTIONS = {
    "fashion": "subtle handheld sway, she shifts her weight, tucks her hair behind her ear and smiles, breeze moving her hair",
    "food": "static handheld with micro-shake, steam rising, she picks at the food and reacts with a small genuine laugh",
    "lifestyle": "very slow lateral drift, breeze in her hair, she glances at the view then back with a soft smile",
    "everyday": "static handheld with micro-shake, she laughs naturally and adjusts her hair, cozy ambient motion",
    "experience": "gentle handheld pan, she looks around and points at something off-screen, candid energy",
    "swim": "static handheld with micro-shake, sunlight sparkling on the water, she splashes her feet gently and laughs",
}


def generate_reel_video(image_url: str, motion: str, duration: int = 5) -> str:
    """Animate a generated still into a short vertical reel. Identity comes
    free - image-to-video animates the exact frame. Primary engine:
    seedance lite (native 9:16, cheap); fallback: hailuo-02 standard."""
    prompt = (motion + ". The camera keeps its distance - never zoom toward "
              "the face. The face stays identical to the source frame the "
              "whole time - no morphing, no warping. Exactly one person. "
              "Natural color balance preserved, no color drift.")
    try:
        return submit_and_poll("/bytedance/seedance/v1/lite/image-to-video", {
            "prompt": prompt,
            "image_url": image_url,
            "duration": duration,
            "resolution": "720",
            "aspect_ratio": "9:16",
        }, timeout=900)
    except RuntimeError as e:
        print(f"  seedance failed ({str(e)[:100]}), trying hailuo", file=sys.stderr)
        return submit_and_poll("/minimax/hailuo-02/standard/image-to-video", {
            "prompt": prompt,
            "image_url": image_url,
            "duration": 6,
            "resolution": "768P",
        }, timeout=900)


ASSETS_BRANCH = "aimodels-assets"
APPROVED_STATUSES = {"approved", "scheduled", "published"}


def gh_api(*args):
    r = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    return json.loads(r.stdout) if r.stdout.strip() else {}


def raw_hosted_file(local: Path, name: str) -> str:
    """Host a local file on the aimodels-assets branch and return its
    raw.githubusercontent URL. The video engine (like nano-banana in the
    rework loop) rejects release-asset URLs - they serve
    application/octet-stream - while raw serves a real image type.

    The request body rides in a --input file: a multi-MB image as a
    base64 argv element dies on the kernel's 128KB per-argument cap
    ("[Errno 7] Argument list too long", run #12)."""
    import base64
    data = local.read_bytes()
    try:
        gh_api(f"repos/{REPO_SLUG}/git/ref/heads/{ASSETS_BRANCH}")
    except Exception:
        main_sha = gh_api(f"repos/{REPO_SLUG}/git/ref/heads/main")["object"]["sha"]
        gh_api(f"repos/{REPO_SLUG}/git/refs", "-f", f"ref=refs/heads/{ASSETS_BRANCH}",
               "-f", f"sha={main_sha}")
    path = f"media-src/{name}"
    payload = {"message": f"Reel source frame {name}",
               "branch": ASSETS_BRANCH,
               "content": base64.b64encode(data).decode()}
    body = local.parent / f"{name}.putbody.json"

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


def download_to(url: str, dest: Path) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        dest.write_bytes(r.read())
    return dest


def crop_to_9x16(src: Path, dest: Path) -> Path:
    """Center-crop to the 9:16 reel frame. The i2v engines follow the
    source image's aspect regardless of their aspect_ratio param (the
    2026-08-17 reel came out 3:4 from a 3:4 still), so the still itself
    must already be 9:16. Our subjects are horizontally centered, so a
    center crop is safe - and the QA gate checks the result anyway."""
    from PIL import Image
    im = Image.open(src).convert("RGB")
    target_w = round(im.height * 9 / 16)
    if im.width > target_w:
        x = (im.width - target_w) // 2
        im = im.crop((x, 0, x + target_w, im.height))
    im.save(dest, "JPEG", quality=92)
    return dest


def latest_approved_image(batch: dict, char_id: str) -> dict | None:
    """The newest human-approved photo of this character that no reel has
    animated yet. An image the owner already scheduled or published is the
    strongest identity + authenticity guarantee there is - it passed his
    own review - so reels prefer it over a fresh unreviewed still."""
    used = {p.get("source_item") for p in batch.get("posts", [])
            if p.get("type") == "reel"}
    cands = [p for p in batch.get("posts", [])
             if p.get("char") == char_id and p.get("type", "post") == "post"
             and p.get("status") in APPROVED_STATUSES
             and p.get("image") and not str(p["image"]).endswith(".mp4")
             and p.get("id") not in used]
    if not cands:
        return None
    return max(cands, key=lambda p: (str(p.get("date", "")),
                                     str(p.get("generatedAt", ""))))


def still_qa(local_still: Path, char: dict, work_dir: Path) -> tuple[bool, str]:
    """Vision gate before video credits are spent: the candidate frame must
    show exactly one person, the right person, in a believable real-life
    scene - not a copy of the studio reference sheet (the 2026-08-17
    failure: two duplicated figures on a white backdrop). Fails open: no
    key / API trouble never blocks generation, it only skips the check."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return True, "no key - QA skipped"
    import base64
    try:
        ref_local = work_dir / f"{char['id']}-qa-ref.png"
        if not ref_local.exists():
            download_to(reference_candidates(char)[0], ref_local)
        import anthropic
        client = anthropic.Anthropic()
        img = lambda p, mt: {"type": "image", "source": {
            "type": "base64", "media_type": mt,
            "data": base64.b64encode(p.read_bytes()).decode()}}
        msg = client.messages.create(
            model=CAPTION_MODEL, max_tokens=200,
            messages=[{"role": "user", "content": [
                img(ref_local, "image/png"),
                img(local_still, "image/jpeg"),
                {"type": "text", "text":
                    f"Image 1 is the studio reference sheet for {char['name']}, "
                    "a virtual influencer. Image 2 is a candidate frame for her "
                    "Instagram reel. Answer ONLY with JSON: "
                    '{"ok": true/false, "reason": "..."}. ok is true only if '
                    "ALL hold in image 2: exactly one person; she is clearly "
                    "the same person as the reference (face, hair color); the "
                    "setting is a believable real-life scene and NOT a white "
                    "studio backdrop or a copy of the reference sheet's layout; "
                    "no obvious AI artifacts (duplicated figure, extra limbs, "
                    "warped face). reason: a short English phrase saying what "
                    "fails, or 'ok'."}]}])
        text = " ".join(b.text for b in msg.content if b.type == "text")
        m = re.search(r"\{.*\}", text, re.S)
        verdict = json.loads(m.group(0)) if m else {"ok": True, "reason": "unparsable"}
        return bool(verdict.get("ok")), str(verdict.get("reason", ""))[:200]
    except Exception as e:
        print(f"  still QA skipped ({e})", file=sys.stderr)
        return True, "qa error - skipped"


def build_reel_still(char: dict, batch: dict, theme: str, lessons: dict,
                     item_id: str, out_dir: Path):
    """Produce the QA-approved 9:16 frame a reel will animate. Returns
    (local path, source item or None, prompt used or None, scene or None).

    Order: an already-approved photo of the character first; otherwise a
    fresh still against the single-figure CASTING reference (the two-view
    sheet gets copied outright often enough that prompt guards alone lost
    twice in run #11), rotating through the theme's scene variants with
    each QA verdict folded into the next prompt. A frame that never passes
    QA raises - better no reel today than an off-identity one."""
    raw = out_dir / f"{item_id}-src"
    still = out_dir / f"{item_id}-still.jpg"
    src = latest_approved_image(batch, char["id"])
    if src:
        crop_to_9x16(download_to(src["image"], raw), still)
        ok, why = still_qa(still, char, out_dir)
        if ok:
            return still, src, None, None
        print(f"  approved source failed QA ({why}) - generating fresh",
              file=sys.stderr)
    scenes = SCENES.get(theme) or SCENES["everyday"]
    base = random.Random(f"{item_id}:{char['id']}").randrange(len(scenes))
    extra = ""
    why = ""
    for attempt in range(3):
        scene = scenes[(base + attempt) % len(scenes)].format(city=char["city"])
        prompt = (IDENTITY_PREFIX + scene + REALISM_SUFFIX
                  + avoid_clause(lessons, char["id"]) + extra)
        try:
            url = generate_with_refs(char, prompt, aspect="9:16", kind="casting")
        except RuntimeError as e:
            if "aspect" not in str(e).lower():
                raise
            # engine build without 9:16 support - take 3:4 and crop
            url = generate_with_refs(char, prompt, aspect="3:4", kind="casting")
        crop_to_9x16(download_to(url, raw), still)
        ok, why = still_qa(still, char, out_dir)
        if ok:
            return still, None, prompt, scene
        print(f"  fresh still failed QA (attempt {attempt + 1}: {why})",
              file=sys.stderr)
        extra = f" Strictly avoid: {why}."
    raise RuntimeError(f"reel still failed QA 3 times: {why}")


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


def reference_candidates(char: dict, kind: str = "sheet") -> list[str]:
    """Identity-reference URLs to try in order. The Higgsfield API insists
    on a URL served with an image content-type, so the Pages mirror of the
    aimodels-roster release comes first (image/png, owned by us), then the
    original CDN copy while it exists, then the raw release asset
    (octet-stream - rejected today, kept as a last resort in case their
    validation relaxes).

    kind="casting" returns the single-figure casting portrait instead of
    the two-view sheet. Reel stills use it: the engine sometimes copies
    the sheet's layout outright (two duplicated figures on a white studio
    backdrop - the 2026-08-17 reel, twice more in the retry run), and a
    one-person reference has no such layout to copy."""
    if kind == "casting" and char.get("casting"):
        urls = [f"https://dudimaman.github.io/video-editor/refs/"
                f"{char['casting'].rsplit('/', 1)[-1]}"]
        if char.get("casting_src"):
            urls.append(char["casting_src"])
        urls.append(char["casting"])
        return urls
    urls = [f"https://dudimaman.github.io/video-editor/refs/{char['id']}-sheet.png"]
    if char.get("sheet_src"):
        urls.append(char["sheet_src"])
    if char.get("sheet") and char["sheet"] not in urls:
        urls.append(char["sheet"])
    return urls


def pick_theme(rng: random.Random, plan: dict) -> str:
    if rng.random() < plan.get("everyday_ratio", 0.4):
        return "everyday"
    other = [t for t in plan.get("themes", list(SCENES)) if t != "everyday" and t in SCENES]
    return rng.choice(other) if other else "everyday"


def planned_types(plan: dict, weekday: str, char_id: str = None,
                  today: str = None) -> list[str]:
    """What this character produces today. reels/stories accept an optional
    "chars" allowlist so video (expensive) can roll out account by account
    - e.g. only the character whose profile is live - and an optional
    "extra_dates" list of ISO dates for one-off runs outside the weekly
    schedule (e.g. regenerating a rejected reel the same day)."""
    types = ["post"] * int(plan.get("daily_posts_per_character", 1))
    for t in ("reels", "stories"):
        cfg = plan.get(t) or {}
        if not cfg.get("enabled"):
            continue
        if weekday not in cfg.get("days", []) and \
                (today is None or today not in cfg.get("extra_dates", [])):
            continue
        allow = cfg.get("chars")
        if allow and char_id is not None and char_id not in allow:
            continue
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

    # Idempotency: a (char, type) that already has an item generated today
    # is done - a re-run only fills the gaps (e.g. after a partial failure).
    # Exception: a REJECTED reel does not count - the owner rejecting the
    # day's reel means "make a better one", so a re-run (plan.json push)
    # produces a replacement. Rejected posts stay counted: those improve
    # via the lessons loop in the next day's batch instead.
    have = {(p.get("char"), p.get("type", "post"))
            for p in batch.get("posts", [])
            if str(p.get("generatedAt", ""))[:10] == today
            and not (p.get("type") == "reel" and p.get("status") == "rejected")}
    existing_ids = {p.get("id") for p in batch.get("posts", [])}

    out_dir.mkdir(parents=True, exist_ok=True)

    # The learning loop: distill Dudi's rejection reasons into avoid-rules
    # and bake them into every prompt. The refreshed lessons ride to the
    # repo in the commit phase (out_dir survives; the repo tree is reset).
    lessons = distill_lessons(batch)
    (out_dir / "lessons.json").write_text(
        json.dumps(lessons, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    release_tag = f"aimodels-{today}"
    new_items, failures = [], 0
    for char in roster:
        for typ in planned_types(plan, weekday, char["id"], today):
            if (char["id"], typ) in have:
                print(f"[{char['id']}/{typ}] already generated today - skip")
                continue
            rng = random.Random(f"{today}:{char['id']}:{typ}")
            theme = pick_theme(rng, plan)
            scene = rng.choice(SCENES[theme]).format(city=char["city"])
            prompt = (IDENTITY_PREFIX + scene + REALISM_SUFFIX
                      + avoid_clause(lessons, char["id"]))
            # A replacement for a same-day rejected item needs its own id -
            # ids double as release asset names, and clobbering the old
            # files would silently rewrite the rejected item's media.
            item_id = f"{char['id']}-{today}-{typ}"
            n = 2
            while item_id in existing_ids:
                item_id = f"{char['id']}-{today}-{typ}-{n}"
                n += 1
            existing_ids.add(item_id)
            try:
                print(f"[{char['id']}/{typ}] {theme}: generating...")
                item = {
                    "id": item_id,
                    "char": char["id"],
                    "type": typ,
                    "theme": theme,
                    "date": today,
                    "time": (plan.get("post_times_by_char") or {}).get(char["id"], "19:00"),
                    "status": "pending",
                    "generatedAt": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    "prompt": prompt,
                }
                if typ == "reel":
                    # The frame to animate: an already-approved photo of the
                    # character when one exists (identity + scene the owner
                    # already signed off on), else a fresh QA-gated still.
                    still_local, src_item, reel_prompt, reel_scene = \
                        build_reel_still(char, batch, theme, lessons,
                                         item_id, out_dir)
                    if src_item:
                        theme = src_item.get("theme", theme)
                        scene = f"a candid {theme} moment in {char['city']}"
                        item["theme"] = theme
                        item["source_item"] = src_item["id"]
                        prompt = f"animated from approved photo {src_item['id']}"
                        print(f"[{char['id']}/{typ}] animating approved "
                              f"photo {src_item['id']}")
                    else:
                        # the prompt/scene the QA-passing attempt really used
                        prompt, scene = reel_prompt, reel_scene
                    item["prompt"] = prompt
                    motion = REEL_MOTIONS.get(theme, REEL_MOTIONS["everyday"])
                    still_perm, thumb = persist_image_and_thumb(
                        still_local, release_tag, f"{item_id}-still.jpg", out_dir)
                    # The i2v engine needs an image-content-type URL; the
                    # release asset just persisted serves octet-stream.
                    engine_url = raw_hosted_file(still_local, f"{item_id}-still.jpg")
                    print(f"[{char['id']}/{typ}] animating ({motion[:50]}...)")
                    video_url = generate_reel_video(
                        engine_url, motion,
                        int((plan.get("reels") or {}).get("duration_s", 5)))
                    video_perm = persist_image(
                        video_url, release_tag, f"{item_id}.mp4", out_dir)
                    item.update({"image": video_perm, "still": still_perm,
                                 **({"thumb": thumb} if thumb else {}),
                                 "prompt": f"{prompt} | motion: {motion}"})
                else:
                    url = generate_with_refs(char, prompt)
                    permanent, thumb = persist_image_and_thumb(
                        url, release_tag, f"{item_id}.jpg", out_dir)
                    item.update({"image": permanent,
                                 **({"thumb": thumb} if thumb else {})})
                item["caption"] = make_caption(char, scene)
                new_items.append(item)
                print(f"[{char['id']}/{typ}] done -> {item_id}")
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


def commit_batch(mutate, message: str, extra_paths=()) -> int:
    """Race-safe batch.json commit shared by the daily run, the backfill
    and the intake: reset to the freshest origin/main, apply `mutate(batch)`
    on top (return False for nothing-to-do), push with retries. The final
    state always wins over concurrent 'save decisions' writes from the tab
    with no possible conflict. `extra_paths` are additional repo-relative
    files the mutator (re)writes that must ride in the same commit (e.g.
    clearing the intake queue)."""
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com")
    path = REPO_ROOT / "data" / "aimodels" / "batch.json"
    for attempt in range(1, 6):
        git("fetch", "origin", "main")
        git("reset", "--hard", "origin/main")
        batch = load_json(path, {"week": "", "posts": []})
        if not mutate(batch):
            print("nothing to commit")
            return 0
        path.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        git("add", "data/aimodels/batch.json", *extra_paths)
        git("commit", "-m", message)
        push = git("push", "origin", "main", check=False)
        if push.returncode == 0:
            print(f"pushed on attempt {attempt}")
            return 0
        print(f"push rejected (attempt {attempt}): {(push.stderr or '').strip()[:200]}")
        time.sleep(2 * attempt)
    print("ERROR: could not push batch.json after 5 attempts", file=sys.stderr)
    return 1


def phase_commit(out_dir: Path) -> int:
    items_file = out_dir / "new_items.json"
    new_items = json.loads(items_file.read_text()) if items_file.exists() else []
    lessons_file = out_dir / "lessons.json"
    new_lessons = lessons_file.read_text() if lessons_file.exists() else None
    if not new_items and not new_lessons:
        print("no new items to commit")
        return 0
    week = datetime.date.today().isocalendar()

    def mutate(batch):
        existing = {p.get("id") for p in batch.get("posts", [])}
        added = [i for i in new_items if i["id"] not in existing]
        changed = bool(added)
        if added:
            batch["posts"] = batch.get("posts", []) + added
            batch["week"] = f"{week[0]}-W{week[1]:02d}"
            print(f"appending {len(added)} item(s)")
        if new_lessons is not None:
            old = LESSONS_PATH.read_text() if LESSONS_PATH.exists() else ""
            if new_lessons != old:
                LESSONS_PATH.write_text(new_lessons, encoding="utf-8")
                print("lessons.json refreshed")
                changed = True
        return changed

    return commit_batch(
        mutate,
        f"AI models: daily batch {datetime.date.today().isoformat()} ({len(new_items)} items)",
        extra_paths=("data/aimodels/lessons.json",))


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
