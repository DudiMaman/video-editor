#!/usr/bin/env python3
"""Distribution sync for the app-marketing side (Planty and future apps):
push owner-approved edited videos from the ledger to each app's
Instagram through Zernio.

This is the apps' twin of distribute_aimodels.py, against a SEPARATE
Zernio account: the workflow maps the ZERNIO_APPS_API_KEY secret into
ZERNIO_API_KEY, so the shared driver needs no changes and the
characters' account stays isolated.

Human approval is the gate, exactly like the tab flow:
- An approved video the owner placed on the Gantt (entry.schedule =
  {date, time}) is sent once with scheduledFor (asset-local time, tz
  from asset.tz or Asia/Jerusalem, converted to UTC); Zernio publishes
  on time.
- An approved video flagged publishNow: true publishes immediately.

Media: ledger outputs are release assets (application/octet-stream,
which Zernio's URL fetcher rejects), so the video bytes are uploaded to
Zernio's own storage via /v1/media/presign and the returned publicUrl
rides in mediaItems. The caption goes out exactly as it appears in the
tab.

State on the entry: zernioPostId (idempotency key - never re-sent),
zernioPostUrl, distribution {via, state, scheduledFor, attempts,
error?}. When the platform confirms publication the entry flips to
status "published" with published_at/published_to - i.e. the video
moves itself to the "סרטונים שעלו לרשת" tab.

Auto-wiring: when no asset carries a zernioAccountId yet, exactly one
asset exists and exactly one active Instagram account is connected in
this Zernio account, the two are wired together in assets.json - the
Planty bootstrap needs no manual id hunting.

Runs from distribute-apps.yml: ledger.json pushes (tab saves), hourly
status sync, manual dispatch. Exits quietly while ZERNIO_APPS_API_KEY
is absent.
"""
import datetime
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_daily import (  # noqa: E402
    REPO_ROOT, REPO_SLUG, USER_AGENT, git, load_json,
)
import distributors  # noqa: E402

DISTRIBUTOR = "zernio"
DEFAULT_TZ = "Asia/Jerusalem"
APPROVED = "approved"
MAX_ATTEMPTS = 3
LEDGER = REPO_ROOT / "data" / "ledger.json"
ASSETS = REPO_ROOT / "assets.json"


def now_z() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def scheduled_at_utc(entry: dict, asset: dict) -> str | None:
    sched = entry.get("schedule") or {}
    try:
        local = datetime.datetime.strptime(
            f"{sched['date']} {sched.get('time') or '18:00'}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=ZoneInfo(asset.get("tz") or DEFAULT_TZ))
        return local.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def output_url(entry: dict) -> str | None:
    out = str(entry.get("output_asset") or "")
    if "/" not in out:
        return None
    tag, name = out.split("/", 1)
    return f"https://github.com/{REPO_SLUG}/releases/download/{tag}/{name}"


def dist_state(e: dict) -> str:
    return ((e.get("distribution") or {}).get("state")) or ""


def attempts(e: dict) -> int:
    return int((e.get("distribution") or {}).get("attempts") or 0)


def asset_of(entry: dict, assets: list) -> dict | None:
    a = next((x for x in assets
              if str(x.get("id")) == str(entry.get("asset"))), None)
    return a if a and a.get("zernioAccountId") else None


def plan_actions(ledger: list, assets: list) -> list[tuple[str, str, dict]]:
    """Pure decision pass (network-free, unit-testable)."""
    actions = []
    for e in ledger:
        asset = asset_of(e, assets)
        if not asset:
            continue
        vid = e.get("video_id")
        sent = bool(e.get("zernioPostId"))
        status = e.get("status")
        if status == APPROVED and e.get("publishNow") and not sent:
            if attempts(e) < MAX_ATTEMPTS:
                actions.append((vid, "publish", {"asset": asset}))
        elif status == APPROVED and (e.get("schedule") or {}).get("date") and not sent:
            if attempts(e) >= MAX_ATTEMPTS:
                continue
            when = scheduled_at_utc(e, asset)
            if when and when > now_z():
                actions.append((vid, "schedule", {"asset": asset, "when": when}))
        elif status == APPROVED and sent:
            when = scheduled_at_utc(e, asset)
            sent_for = (e.get("distribution") or {}).get("scheduledFor")
            if when and sent_for and when != sent_for and when > now_z():
                actions.append((vid, "reschedule", {"asset": asset, "when": when}))
            elif dist_state(e) in ("scheduled", "publishing", "sent"):
                actions.append((vid, "sync", {}))
        elif sent and not e.get("published_at") and status != APPROVED:
            # approval was withdrawn after the send (back to editing /
            # rejected) - take it off the distributor's calendar too
            if dist_state(e) != "canceled":
                actions.append((vid, "cancel", {}))
    return actions


def upload_output(entry: dict, work_dir: Path) -> dict:
    """Release asset -> local bytes -> Zernio storage -> media dict."""
    from distributors import zernio
    url = output_url(entry)
    if not url:
        raise RuntimeError("entry has no output_asset")
    local = work_dir / f"{entry['video_id']}.mp4"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as r:
        local.write_bytes(r.read())
    hosted = zernio.upload_media(local, "video/mp4", local.name)
    return {"type": "video", "url": hosted, "mimeType": "video/mp4"}


def execute(actions, ledger, work_dir) -> dict:
    driver = distributors.get(DISTRIBUTOR)
    by_id = {e.get("video_id"): e for e in ledger}
    patches = {}
    for vid, verb, ctx in actions:
        e = by_id[vid]
        try:
            if verb == "publish":
                res = driver.publish_now(ctx["asset"]["zernioAccountId"],
                                         upload_output(e, work_dir),
                                         e.get("caption") or "")
                published = res["status"] == "published" or bool(res["postUrl"])
                patches[vid] = {
                    "zernioPostId": res["externalId"],
                    **({"zernioPostUrl": res["postUrl"]} if res["postUrl"] else {}),
                    **({"status": "published", "published_at": now_z(),
                        "published_to": sorted(set((e.get("published_to") or [])
                                                   + ["instagram"]))}
                       if published else {}),
                    "publishNow": None,
                    "distribution": {"via": DISTRIBUTOR,
                                     "state": "published" if published else "publishing"},
                }
            elif verb in ("schedule", "reschedule"):
                if verb == "reschedule":
                    try:
                        driver.cancel_post(e["zernioPostId"])
                    except RuntimeError as err:
                        print(f"  [{vid}] cancel before reschedule failed: {err}",
                              file=sys.stderr)
                res = driver.schedule_post(ctx["asset"]["zernioAccountId"],
                                           upload_output(e, work_dir),
                                           e.get("caption") or "",
                                           ctx["when"])
                patches[vid] = {
                    "zernioPostId": res["externalId"],
                    "distribution": {"via": DISTRIBUTOR, "state": "scheduled",
                                     "scheduledFor": ctx["when"]},
                }
            elif verb == "cancel":
                driver.cancel_post(e["zernioPostId"])
                patches[vid] = {"distribution": {**(e.get("distribution") or {}),
                                                 "state": "canceled"}}
            elif verb == "sync":
                res = driver.get_post(e["zernioPostId"])
                if res["status"] == "published":
                    patches[vid] = {
                        "status": "published", "published_at": now_z(),
                        "published_to": sorted(set((e.get("published_to") or [])
                                                   + ["instagram"])),
                        **({"zernioPostUrl": res["postUrl"]} if res["postUrl"] else {}),
                        "distribution": {**(e.get("distribution") or {}),
                                         "state": "published"}}
                elif res["status"] in ("failed", "partial"):
                    patches[vid] = {
                        "distribution": {**(e.get("distribution") or {}),
                                         "state": "failed",
                                         "error": f"zernio status {res['status']}"}}
            print(f"[{vid}] {verb}: ok")
        except Exception as err:
            print(f"[{vid}] {verb} FAILED: {err}", file=sys.stderr)
            patches[vid] = {
                "distribution": {**(e.get("distribution") or {}),
                                 "via": DISTRIBUTOR, "state": "failed",
                                 "attempts": attempts(e) + 1,
                                 "error": str(err)[:300]}}
    return patches


def autowire_assets() -> None:
    """One asset + one connected Instagram account + nothing wired yet ->
    wire them, so the Planty bootstrap needs no manual id hunting."""
    from distributors import zernio
    assets = load_json(ASSETS, [])
    if not assets or any(a.get("zernioAccountId") for a in assets):
        return
    if len(assets) != 1:
        print("multiple assets and none wired - set zernioAccountId in the "
              "assets tab")
        return
    ig = [a for a in zernio.list_accounts()
          if a.get("platform") == "instagram" and a.get("isActive", True)]
    if len(ig) != 1:
        print(f"{len(ig)} active Instagram account(s) connected - "
              "cannot auto-wire unambiguously")
        return
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com")
    for attempt in range(3):
        git("fetch", "origin", "main")
        git("reset", "--hard", "origin/main")
        assets = load_json(ASSETS, [])
        if any(a.get("zernioAccountId") for a in assets) or len(assets) != 1:
            return
        assets[0]["zernioAccountId"] = str(ig[0]["_id"])
        ASSETS.write_text(json.dumps(assets, indent=1) + "\n", encoding="utf-8")
        git("add", "assets.json")
        git("commit", "-m",
            f"Apps: wire Zernio account for {assets[0].get('name', assets[0]['id'])}")
        if git("push", "origin", "main", check=False).returncode == 0:
            print(f"auto-wired {assets[0]['id']} -> Zernio {ig[0].get('username')}")
            return
        time.sleep(2 * (attempt + 1))
    print("::warning::could not push assets.json auto-wire", file=sys.stderr)


def commit_ledger(patches: dict, message: str) -> int:
    """Race-safe ledger update: reset to fresh origin/main, apply the
    patches by video_id, push with retries (same pattern as commit_batch;
    a value of None removes the key)."""
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com")
    for attempt in range(1, 6):
        git("fetch", "origin", "main")
        git("reset", "--hard", "origin/main")
        ledger = load_json(LEDGER, [])
        changed = False
        for e in ledger:
            patch = patches.get(e.get("video_id"))
            if not patch:
                continue
            for k, v in patch.items():
                if v is None:
                    if k in e:
                        del e[k]
                        changed = True
                elif e.get(k) != v:
                    e[k] = v
                    changed = True
        if not changed:
            print("nothing to commit")
            return 0
        LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
        git("add", "data/ledger.json")
        git("commit", "-m", message)
        if git("push", "origin", "main", check=False).returncode == 0:
            print(f"pushed on attempt {attempt}")
            return 0
        time.sleep(2 * attempt)
    print("ERROR: could not push ledger.json", file=sys.stderr)
    return 1


def main() -> int:
    if not os.environ.get("ZERNIO_API_KEY"):
        print("::notice::ZERNIO_APPS_API_KEY is not set - app distribution "
              "skipped (add the repo secret once the apps' Zernio account "
              "exists)")
        return 0
    autowire_assets()
    assets = load_json(ASSETS, [])
    if not any(a.get("zernioAccountId") for a in assets):
        print("no asset has a zernioAccountId yet - nothing to distribute")
        return 0
    ledger = load_json(LEDGER, [])
    actions = plan_actions(ledger, assets)
    if not actions:
        print("nothing to distribute")
        return 0
    work_dir = Path("out/distribute-apps")
    work_dir.mkdir(parents=True, exist_ok=True)
    patches = execute(actions, ledger, work_dir)
    if not patches:
        return 0
    rc = commit_ledger(patches, "Apps: distribution sync (zernio)")
    failures = [v for v, p in patches.items()
                if (p.get("distribution") or {}).get("error")]
    if failures:
        print(f"::warning::{len(failures)} video(s) failed distribution: "
              + ", ".join(failures))
    return rc


if __name__ == "__main__":
    sys.exit(main())
