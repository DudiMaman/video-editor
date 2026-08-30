#!/usr/bin/env python3
"""Distribution sync for the AI-models tab: push owner-approved items to
each brand's socials through that brand's distributor.

Two drivers, chosen PER BRAND via roster.json:
- zernio (default): the original wiring - zernioAccountId + the
  ZERNIO_API_KEY secret. Untouched; remains the fallback.
- buffer: one Buffer Free account per brand (3 channels: facebook /
  instagram / tiktok), key in the BUFFER_TOKEN_<BRAND> secret,
  channel ids auto-wired into char.bufferChannels on the first run.
  Buffer quirks handled here: media by public URL only, a 10-posts-
  per-channel queue cap (items HOLD in queue_wait and retry next run -
  never failed, never lost), and a 3,000-calls/30-days key quota
  (status polling is skipped until a post's dueAt is near; every call
  is logged).

Human approval is the gate - nothing is ever sent unless Dudi acted:
- An item he scheduled in the Gantt (status "scheduled" with date+time)
  is sent once to the distributor with its scheduledFor; the distributor
  publishes at the right moment - we never fire the post ourselves.
- An item he clicked "פרסם עכשיו" on (publishNow: true in batch.json) is
  published immediately.

State written back onto the item (race-safe via commit_batch):
  zernioPostId      distributor's post id - THE idempotency key: an item
                    that has one is never sent again
  zernioPostUrl     public URL of the published post (when known)
  distribution      {via, state: sent|publishing|scheduled|published|
                     failed|canceled, scheduledFor, attempts, error?}
  publishedAt       stamped when the platform confirms publication

Other rules:
- Rescheduling: if the item's date/time no longer matches what was sent,
  the old distributor post is canceled and a new one is created.
- Rejecting a sent-but-unpublished item cancels it at the distributor.
- Items whose character has no zernioAccountId in roster.json are
  skipped (the tab shows "connect in Zernio" for those).
- A failing item is retried at most MAX_ATTEMPTS times, then left with
  its error until the owner changes it.
- Media URLs must serve a real Content-Type (Zernio fetches them):
  images are raw-hosted on the aimodels-assets branch at send time;
  reel videos use the Pages media mirror (real video/mp4) with the reel
  still as cover.

Runs in GitHub Actions (distribute-aimodels.yml): on batch.json pushes
(a tab save right after scheduling / publish-now), hourly for status
sync, and on manual dispatch. Exits quietly when ZERNIO_API_KEY is
absent so the repo works before Zernio is set up.
"""
import datetime
import json
import sys
import os
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_daily import (  # noqa: E402
    REPO_ROOT, USER_AGENT, commit_batch, download_to, load_json,
    raw_hosted_file,
)
import distributors  # noqa: E402
from distributors.buffer import QueueFullError  # noqa: E402
import zernio_inbox  # noqa: E402

PAGES_BASE = "https://dudimaman.github.io/video-editor"
DISTRIBUTOR = "zernio"  # the default; per-brand override via char.distributor
ACCOUNT_FIELD = "zernioAccountId"
ROSTER = REPO_ROOT / "data" / "aimodels" / "roster.json"
APPROVED_FOR_SEND = {"scheduled", "approved"}
MAX_ATTEMPTS = 3


# ------------------------------------------------- per-brand distributor
# Shared with distribute_apps.py and the owner scripts - one
# implementation of "which driver, which key, which channels".
from buffer_wiring import (  # noqa: E402
    distributor_of, key_env_of as buffer_key_env_of,
    targets_of as buffer_targets_of, with_key as with_buffer_key,
)
import buffer_wiring  # noqa: E402


def brand_ready(char: dict) -> bool:
    """Can this brand publish right now? zernio: has a connected account
    id. buffer: has its token secret AND wired channels (auto-wired when
    the token exists). A buffer brand with no token is SKIPPED - never a
    silent fallback to Zernio."""
    if distributor_of(char) == "buffer":
        return buffer_wiring.ready(char)
    return bool(char.get(ACCOUNT_FIELD))


def scheduled_at_utc(item: dict, char: dict) -> str | None:
    """date+time are the character's local wall clock (each character
    posts in her own city's timezone); the distributor gets unambiguous
    UTC with a trailing Z."""
    try:
        local = datetime.datetime.strptime(
            f"{item['date']} {item.get('time') or '19:00'}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=ZoneInfo(char.get("tz") or "UTC"))
        return local.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def hosted_image(url: str, name: str, work_dir: Path) -> str:
    """Release-asset URLs serve application/octet-stream, which Zernio's
    media fetcher rejects - serve a byte-exact copy from raw (image/*)."""
    ext = Path(str(url).split("?")[0]).suffix or ".jpg"
    local = work_dir / f"{name}{ext}"
    download_to(url, local)
    return raw_hosted_file(local, local.name)


def media_for(item: dict, work_dir: Path) -> dict:
    # isAi: True on everything here - the characters' images and reels
    # are fully synthetic, and Meta's self-disclosure flag must say so.
    # The driver default is NO label (real footage is the norm elsewhere),
    # so this pipeline opts in explicitly.
    typ = item.get("type", "post")
    if typ == "reel":
        video = f"{PAGES_BASE}/media/{str(item['image']).rsplit('/', 1)[-1]}"
        media = {"type": "video", "url": video, "mimeType": "video/mp4",
                 "isAi": True}
        if item.get("still"):
            try:
                media["cover"] = hosted_image(item["still"],
                                              f"dist-{item['id']}-cover", work_dir)
            except Exception as e:  # a cover is optional - never block the reel
                print(f"  [{item['id']}] cover hosting failed: {e}", file=sys.stderr)
        return media
    media = {"type": "image",
             "url": hosted_image(item["image"], f"dist-{item['id']}", work_dir),
             "isAi": True}
    if typ == "story":
        media["story"] = True
    return media


def dist_state(item: dict) -> str:
    return ((item.get("distribution") or {}).get("state")) or ""


def attempts(item: dict) -> int:
    return int((item.get("distribution") or {}).get("attempts") or 0)


def now_z() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def plan_actions(batch: dict, chars: dict) -> list[tuple[str, str, dict]]:
    """Decide what to do; return (item_id, verb, context) tuples. All
    network work happens in execute(); this stays pure for testing."""
    actions = []
    for item in batch.get("posts", []):
        char = chars.get(item.get("char"))
        if not char:  # character not connected in Zernio
            continue
        status = item.get("status")
        sent = bool(item.get("zernioPostId"))
        if item.get("publishNow") and not sent:
            if attempts(item) >= MAX_ATTEMPTS:
                continue
            actions.append((item["id"], "publish", {"char": char}))
        elif status in APPROVED_FOR_SEND and not sent:
            if attempts(item) >= MAX_ATTEMPTS:
                continue
            when = scheduled_at_utc(item, char)
            if not when:
                continue
            if when <= now_z():
                # A past slot would publish immediately - that needs the
                # owner's explicit publishNow, not a stale schedule.
                continue
            actions.append((item["id"], "schedule", {"char": char, "when": when}))
        elif status in APPROVED_FOR_SEND and sent:
            when = scheduled_at_utc(item, char)
            sent_for = (item.get("distribution") or {}).get("scheduledFor")
            if when and sent_for and when != sent_for and when > now_z():
                actions.append((item["id"], "reschedule",
                                {"char": char, "when": when}))
            elif dist_state(item) in ("sent", "scheduled", "publishing"):
                actions.append((item["id"], "sync", {}))
        elif status == "rejected" and sent and not item.get("publishedAt"):
            if dist_state(item) != "canceled":
                actions.append((item["id"], "cancel", {}))
        elif sent and dist_state(item) in ("sent", "publishing"):
            actions.append((item["id"], "sync", {}))
    return actions


def notice(item: dict, char: dict, message: str) -> dict:
    """A distribution-inbox record for one failed character post."""
    if distributor_of(char or {}) == "buffer":
        platform = ",".join(sorted(t["platform"]
                                   for t in buffer_targets_of(char or {}))) or ""
    else:
        platform = "instagram"
    return {
        "source": "aimodels",
        "venture": str((char or {}).get("id") or item.get("char") or ""),
        "ventureName": (char or {}).get("name")
        or str(item.get("char") or "דמות"),
        "platform": platform,
        "postId": item.get("zernioPostId") or "",
        "refId": item.get("id"),
        "level": "error",
        "message": str(message)[:400],
        "contentPreview": (item.get("caption") or "").strip()[:120],
        "scheduledFor": (item.get("distribution") or {}).get("scheduledFor") or "",
        "postUrl": item.get("zernioPostUrl") or "",
    }


def execute(actions, batch, work_dir, chars=None) -> tuple[dict, list]:
    """Run the network side; return (patches, inbox) where inbox is a list
    of Zernio-inbox notice records for the failures surfaced this run.

    Per-brand driver selection: each item runs through its brand's
    distributor (zernio - default - or buffer) under that brand's own
    key. The Buffer post id(s) are stored in the same zernioPostId field
    the whole pipeline (and the tab) already uses as THE idempotency
    key/guard; distribution.via says which service the id belongs to.
    Renaming that field across ledger+UI is a later cleanup - reusing it
    keeps every existing double-send guard intact during the migration."""
    chars = chars or {}
    by_id = {p["id"]: p for p in batch.get("posts", [])}
    patches = {}
    inbox = []
    for item_id, verb, ctx in actions:
        item = by_id[item_id]
        char = ctx.get("char") or chars.get(item.get("char")) or {}
        drv_name = distributor_of(char)
        driver = distributors.get(drv_name)
        if drv_name == "buffer":
            account = buffer_targets_of(char)
            key_ctx = with_buffer_key(os.environ.get(buffer_key_env_of(char), ""))
        else:
            account = char.get(ACCOUNT_FIELD)
            from contextlib import nullcontext
            key_ctx = nullcontext()
        try:
          with key_ctx:
            if verb == "publish":
                res = driver.publish_now(account,
                                         media_for(item, work_dir),
                                         item.get("caption") or "")
                published = res["status"] == "published" or bool(res["postUrl"])
                patches[item_id] = {
                    "zernioPostId": res["externalId"],
                    **({"zernioPostUrl": res["postUrl"]} if res["postUrl"] else {}),
                    **({"status": "published", "publishedAt": now_z()}
                       if published else {}),
                    "publishNow": None,  # None = remove the key
                    "distribution": {"via": drv_name,
                                     **({"urls": res["urls"]} if res.get("urls") else {}),
                                     "state": "published" if published else "publishing"},
                }
            elif verb in ("schedule", "reschedule"):
                # Buffer Free holds at most 10 pending posts per channel.
                # A full queue is NOT a failure: hold the item in
                # queue_wait (attempts untouched) and re-plan next run -
                # a slot frees whenever a queued post publishes.
                if drv_name == "buffer" and verb == "schedule":
                    full = driver.check_queues(account)
                    if full:
                        patches[item_id] = {
                            "distribution": {**(item.get("distribution") or {}),
                                             "via": drv_name,
                                             "state": "queue_wait",
                                             "reason": "queue full: "
                                             + ", ".join(full)}}
                        print(f"[{item_id}] held - Buffer queue full on "
                              f"{', '.join(full)}; retrying next run")
                        continue
                if verb == "reschedule":
                    try:
                        driver.cancel_post(item["zernioPostId"])
                    except RuntimeError as e:
                        print(f"  [{item_id}] cancel before reschedule failed: {e}",
                              file=sys.stderr)
                res = driver.schedule_post(account,
                                           media_for(item, work_dir),
                                           item.get("caption") or "",
                                           ctx["when"])
                patches[item_id] = {
                    "zernioPostId": res["externalId"],
                    "distributedAt": now_z(),
                    "distribution": {"via": drv_name, "state": "scheduled",
                                     "scheduledFor": ctx["when"]},
                }
            elif verb == "cancel":
                driver.cancel_post(item["zernioPostId"])
                patches[item_id] = {
                    "distribution": {**(item.get("distribution") or {}),
                                     "state": "canceled"}}
            elif verb == "sync":
                # Rate-limit frugality (Buffer Free: 3,000 calls/30 days):
                # a post whose slot is still in the future cannot have
                # published - skip the status call entirely until ~5
                # minutes before dueAt.
                if drv_name == "buffer":
                    sched = (item.get("distribution") or {}).get("scheduledFor")
                    if sched and sched > (datetime.datetime.now(datetime.timezone.utc)
                                          + datetime.timedelta(minutes=5)
                                          ).strftime("%Y-%m-%dT%H:%M:%SZ"):
                        continue
                res = driver.get_post(item["zernioPostId"])
                if res["status"] == "published":
                    patches[item_id] = {
                        "status": "published", "publishedAt": now_z(),
                        **({"zernioPostUrl": res["postUrl"]} if res["postUrl"] else {}),
                        "distribution": {**(item.get("distribution") or {}),
                                         **({"urls": res["urls"]} if res.get("urls") else {}),
                                         "state": "published"}}
                elif res["status"] in ("failed", "partial"):
                    msg = zernio_inbox.errors_text(res) \
                        or f"{drv_name} status {res['status']}"
                    patches[item_id] = {
                        "distribution": {**(item.get("distribution") or {}),
                                         "state": "failed", "error": msg[:300]}}
                    for x in (res.get("errors")
                              or [{"platform": "", "message": msg}]):
                        inbox.append(notice(item, char, x.get("message")))
            print(f"[{item_id}] {verb}: ok ({drv_name})")
        except QueueFullError as e:
            # publish-now / reschedule hitting a full queue: same hold.
            patches[item_id] = {
                "distribution": {**(item.get("distribution") or {}),
                                 "via": drv_name, "state": "queue_wait",
                                 "reason": str(e)[:200]}}
            print(f"[{item_id}] held - {e}; retrying next run")
        except Exception as e:
            print(f"[{item_id}] {verb} FAILED: {e}", file=sys.stderr)
            prev = item.get("distribution") or {}
            patches[item_id] = {
                "distribution": {**prev, "via": drv_name, "state": "failed",
                                 "attempts": attempts(item) + 1,
                                 "error": str(e)[:300]}}
            inbox.append(notice(item, char, str(e)))
    return patches, inbox


def main() -> int:
    any_buffer = buffer_wiring.any_buffer_key()
    if not os.environ.get("ZERNIO_API_KEY") and not any_buffer:
        print("::notice::no distributor key is configured (ZERNIO_API_KEY / "
              "BUFFER_TOKEN_<BRAND>) - distribution skipped")
        return 0
    if any_buffer:
        buffer_wiring.autowire_channels(ROSTER)
    roster = load_json(ROSTER, [])
    chars = {c["id"]: c for c in roster if brand_ready(c)}
    if not chars:
        print("no brand is connected to a distributor yet - nothing to distribute")
        return 0
    batch = load_json(REPO_ROOT / "data" / "aimodels" / "batch.json", {"posts": []})
    actions = plan_actions(batch, chars)
    if not actions:
        print("nothing to distribute")
        return 0
    work_dir = Path("out/distribute")
    work_dir.mkdir(parents=True, exist_ok=True)
    patches, inbox = execute(actions, batch, work_dir, chars)
    if inbox:
        zernio_inbox.append(inbox)
    if not patches:
        return 0

    def mutate(fresh: dict):
        changed = False
        for p in fresh.get("posts", []):
            patch = patches.get(p.get("id"))
            if not patch:
                continue
            for k, v in patch.items():
                if v is None:
                    if k in p:
                        del p[k]
                        changed = True
                elif p.get(k) != v:
                    p[k] = v
                    changed = True
        return changed

    rc = commit_batch(mutate, "AI models: distribution sync (zernio)")
    hard_failures = [i for i, p in patches.items()
                     if (p.get("distribution") or {}).get("error")]
    if hard_failures:
        print(f"::warning::{len(hard_failures)} item(s) failed distribution: "
              + ", ".join(hard_failures))
    return rc


if __name__ == "__main__":
    sys.exit(main())
