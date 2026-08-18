#!/usr/bin/env python3
"""Distribution sync for the AI-models tab: push owner-approved items to
Instagram through the configured distributor (Zernio).

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

PAGES_BASE = "https://dudimaman.github.io/video-editor"
DISTRIBUTOR = "zernio"
ACCOUNT_FIELD = "zernioAccountId"
APPROVED_FOR_SEND = {"scheduled", "approved"}
MAX_ATTEMPTS = 3


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
    typ = item.get("type", "post")
    if typ == "reel":
        video = f"{PAGES_BASE}/media/{str(item['image']).rsplit('/', 1)[-1]}"
        media = {"type": "video", "url": video, "mimeType": "video/mp4"}
        if item.get("still"):
            try:
                media["cover"] = hosted_image(item["still"],
                                              f"dist-{item['id']}-cover", work_dir)
            except Exception as e:  # a cover is optional - never block the reel
                print(f"  [{item['id']}] cover hosting failed: {e}", file=sys.stderr)
        return media
    media = {"type": "image",
             "url": hosted_image(item["image"], f"dist-{item['id']}", work_dir)}
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


def execute(actions, batch, work_dir) -> dict:
    """Run the network side; return {item_id: patch}."""
    driver = distributors.get(DISTRIBUTOR)
    by_id = {p["id"]: p for p in batch.get("posts", [])}
    patches = {}
    for item_id, verb, ctx in actions:
        item = by_id[item_id]
        try:
            if verb == "publish":
                res = driver.publish_now(ctx["char"][ACCOUNT_FIELD],
                                         media_for(item, work_dir),
                                         item.get("caption") or "")
                published = res["status"] == "published" or bool(res["postUrl"])
                patches[item_id] = {
                    "zernioPostId": res["externalId"],
                    **({"zernioPostUrl": res["postUrl"]} if res["postUrl"] else {}),
                    **({"status": "published", "publishedAt": now_z()}
                       if published else {}),
                    "publishNow": None,  # None = remove the key
                    "distribution": {"via": DISTRIBUTOR,
                                     "state": "published" if published else "publishing"},
                }
            elif verb in ("schedule", "reschedule"):
                if verb == "reschedule":
                    try:
                        driver.cancel_post(item["zernioPostId"])
                    except RuntimeError as e:
                        print(f"  [{item_id}] cancel before reschedule failed: {e}",
                              file=sys.stderr)
                res = driver.schedule_post(ctx["char"][ACCOUNT_FIELD],
                                           media_for(item, work_dir),
                                           item.get("caption") or "",
                                           ctx["when"])
                patches[item_id] = {
                    "zernioPostId": res["externalId"],
                    "distributedAt": now_z(),
                    "distribution": {"via": DISTRIBUTOR, "state": "scheduled",
                                     "scheduledFor": ctx["when"]},
                }
            elif verb == "cancel":
                driver.cancel_post(item["zernioPostId"])
                patches[item_id] = {
                    "distribution": {**(item.get("distribution") or {}),
                                     "state": "canceled"}}
            elif verb == "sync":
                res = driver.get_post(item["zernioPostId"])
                if res["status"] == "published":
                    patches[item_id] = {
                        "status": "published", "publishedAt": now_z(),
                        **({"zernioPostUrl": res["postUrl"]} if res["postUrl"] else {}),
                        "distribution": {**(item.get("distribution") or {}),
                                         "state": "published"}}
                elif res["status"] in ("failed", "partial"):
                    patches[item_id] = {
                        "distribution": {**(item.get("distribution") or {}),
                                         "state": "failed",
                                         "error": f"zernio status {res['status']}"}}
            print(f"[{item_id}] {verb}: ok")
        except Exception as e:
            print(f"[{item_id}] {verb} FAILED: {e}", file=sys.stderr)
            prev = item.get("distribution") or {}
            patches[item_id] = {
                "distribution": {**prev, "via": DISTRIBUTOR, "state": "failed",
                                 "attempts": attempts(item) + 1,
                                 "error": str(e)[:300]}}
    return patches


def main() -> int:
    if not os.environ.get("ZERNIO_API_KEY"):
        print("::notice::ZERNIO_API_KEY is not set - distribution skipped "
              "(add the repo secret once Zernio is connected)")
        return 0
    roster = load_json(REPO_ROOT / "data" / "aimodels" / "roster.json", [])
    chars = {c["id"]: c for c in roster if c.get(ACCOUNT_FIELD)}
    if not chars:
        print("no character has a zernioAccountId yet - nothing to distribute")
        return 0
    batch = load_json(REPO_ROOT / "data" / "aimodels" / "batch.json", {"posts": []})
    actions = plan_actions(batch, chars)
    if not actions:
        print("nothing to distribute")
        return 0
    work_dir = Path("out/distribute")
    work_dir.mkdir(parents=True, exist_ok=True)
    patches = execute(actions, batch, work_dir)
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
