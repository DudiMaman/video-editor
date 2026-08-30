#!/usr/bin/env python3
"""Distribution sync for the app-marketing side (Planty and future apps):
push owner-approved edited videos from the ledger to each app's connected
socials (TikTok / Instagram / Facebook / YouTube) through Zernio. The
owner picks which of the connected platforms each video goes to on the
schedule calendar (entry.platforms); the default is all of them.

This is the apps' twin of distribute_aimodels.py. Zernio accounts are
PER VENTURE (owner decision: each venture is its own business with its
own free-tier Zernio account, ~2 connected socials each). Key lookup
per asset: the env var named by asset.zernioKeyEnv, else
ZERNIO_KEY_<ASSET_ID_UPPERCASED>, else the legacy shared
ZERNIO_APPS_API_KEY. The workflow maps one repo secret per venture into
those names - adding a venture means adding one secret and one env line
in distribute-apps.yml. The shared driver still reads ZERNIO_API_KEY,
so each action runs with the right key swapped into that variable.

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

Auto-wiring: every venture whose key is configured and whose asset has
no zernioAccountId yet gets wired automatically when its Zernio account
holds exactly one active Instagram account - no manual id hunting.

Runs from distribute-apps.yml: ledger.json pushes (tab saves), hourly
status sync, manual dispatch. Exits quietly while ZERNIO_APPS_API_KEY
is absent.
"""
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_daily import (  # noqa: E402
    REPO_ROOT, REPO_SLUG, USER_AGENT, git, load_json,
)
import distributors  # noqa: E402
from distributors.buffer import QueueFullError  # noqa: E402
import buffer_wiring  # noqa: E402
import zernio_inbox  # noqa: E402

DISTRIBUTOR = "zernio"  # default; a venture opts into buffer in assets.json
DEFAULT_TZ = "Asia/Jerusalem"
APPROVED = "approved"
MAX_ATTEMPTS = 3
LEDGER = REPO_ROOT / "data" / "ledger.json"
ASSETS = REPO_ROOT / "assets.json"
PAGES_BASE = "https://dudimaman.github.io/video-editor"


class MediaWait(Exception):
    """The Pages mirror copy of the video is not live yet - hold the
    entry (state media_wait) and retry next run instead of failing."""


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


def key_env_of(asset: dict) -> str:
    """Env var carrying this venture's Zernio API key. Explicit override
    via asset.zernioKeyEnv (e.g. ZERNIO_KEY_PLANTY for the legacy asset
    id 'sample'), else derived from the asset id."""
    import re
    return asset.get("zernioKeyEnv") or \
        "ZERNIO_KEY_" + re.sub(r"[^A-Za-z0-9]", "_", str(asset.get("id"))).upper()


def key_of(asset: dict) -> str:
    return os.environ.get(key_env_of(asset)) or \
        os.environ.get("ZERNIO_APPS_API_KEY") or ""


@contextmanager
def with_key(key: str):
    """The shared driver reads ZERNIO_API_KEY from the environment; swap
    the venture's key in for the duration of its API calls (the runner is
    single-threaded, so this is safe)."""
    prev = os.environ.get("ZERNIO_API_KEY")
    os.environ["ZERNIO_API_KEY"] = key
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("ZERNIO_API_KEY", None)
        else:
            os.environ["ZERNIO_API_KEY"] = prev


def targets_of(asset: dict) -> list[dict]:
    """The venture's publish targets: the multi-platform zernioTargets
    list when present, else the legacy single Instagram zernioAccountId."""
    tg = [t for t in (asset.get("zernioTargets") or [])
          if t.get("platform") and t.get("accountId")]
    if tg:
        return tg
    if asset.get("zernioAccountId"):
        return [{"platform": "instagram", "accountId": asset["zernioAccountId"]}]
    return []


def connected_targets_of(asset: dict) -> list[dict]:
    """Publish targets through the venture's CURRENT distributor: the
    Buffer channels when it migrated, else the Zernio targets."""
    if buffer_wiring.distributor_of(asset) == "buffer":
        return buffer_wiring.targets_of(asset)
    return targets_of(asset)


def driver_name_for(entry: dict, asset: dict) -> str:
    """Driver for an entry. A post already sent somewhere STAYS with the
    service that holds it (distribution.via) - so Planty's posts that
    were scheduled through Zernio keep syncing/canceling through Zernio
    even after the venture migrated to Buffer. Unsent work follows the
    venture's current distributor."""
    if entry.get("zernioPostId"):
        return (entry.get("distribution") or {}).get("via") or \
            buffer_wiring.distributor_of(asset)
    return buffer_wiring.distributor_of(asset)


def key_ctx_for(drv_name: str, asset: dict):
    """Key context for running `drv_name` calls as this venture."""
    if drv_name == "buffer":
        return buffer_wiring.with_key(
            os.environ.get(buffer_wiring.key_env_of(asset), ""))
    return with_key(key_of(asset))


def pages_media_url(entry: dict) -> str:
    """The stable public URL Buffer gets: the video's copy on the Pages
    site (real video/mp4, no redirect) - mirrored by pages.yml for every
    Buffer venture's pending/recent ledger entry."""
    out = str(entry.get("output_asset") or "")
    tag, name = out.split("/", 1)
    return f"{PAGES_BASE}/media/apps-{tag}-{name.replace('/', '-')}"


def url_live(url: str) -> bool:
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def ensure_pages_media(entry: dict) -> str:
    """Return the entry's live Pages media URL. When the mirror copy is
    not there yet, kick the pages deploy (the proven daily-workflow
    pattern) and poll for a few minutes; still missing -> MediaWait, the
    entry holds and the next hourly run finds the mirror ready."""
    url = pages_media_url(entry)
    if url_live(url):
        return url
    print(f"  media not on Pages yet - dispatching pages.yml and waiting: {url}")
    subprocess.run(["gh", "workflow", "run", "pages.yml", "--ref", "main"],
                   cwd=REPO_ROOT, check=False, capture_output=True, text=True)
    for _ in range(12):
        time.sleep(20)
        if url_live(url):
            return url
    raise MediaWait(url)


def buffer_media(entry: dict) -> dict:
    """isAi False - same reasoning as upload_output: these are real
    commercial app videos, conventionally edited."""
    return {"type": "video", "url": ensure_pages_media(entry),
            "mimeType": "video/mp4", "isAi": False}


def targets_for(asset: dict, entry: dict) -> list[dict]:
    """Publish targets for THIS video: the venture's connected targets,
    narrowed to the platforms the owner kept checked on the schedule
    calendar (entry.platforms, platform-value strings). An empty/absent
    selection means every connected platform - the calendar defaults to
    all checked, so unchecking is the only way to reach a subset."""
    tg = connected_targets_of(asset)
    picked = entry.get("platforms")
    if picked:
        sel = [t for t in tg if t["platform"] in set(picked)]
        if sel:
            return sel
    return tg


errors_text = zernio_inbox.errors_text


def notice(entry: dict, asset: dict, platform: str, message: str,
           level: str = "error", post_url: str = "") -> dict:
    """A Zernio-inbox record for one problem on one video."""
    return {
        "source": "apps",
        "venture": str(asset.get("id")),
        "ventureName": asset.get("name") or str(asset.get("id")),
        "platform": platform or "",
        "postId": entry.get("zernioPostId") or "",
        "refId": entry.get("video_id"),
        "level": level,
        "message": str(message)[:400],
        "contentPreview": (entry.get("caption") or "").strip()[:120],
        "scheduledFor": (entry.get("distribution") or {}).get("scheduledFor")
        or scheduled_at_utc(entry, asset) or "",
        "postUrl": post_url or entry.get("zernioPostUrl") or "",
    }


def asset_of(entry: dict, assets: list) -> dict | None:
    """The entry's venture, when it can act on it. A Buffer venture must
    be ready (token + wired channels) for NEW sends, but an entry already
    sent through Zernio (before the migration) stays actionable via its
    Zernio wiring - so pending Zernio posts keep syncing/canceling."""
    a = next((x for x in assets
              if str(x.get("id")) == str(entry.get("asset"))), None)
    if not a:
        return None
    if buffer_wiring.distributor_of(a) == "buffer":
        if buffer_wiring.ready(a):
            return a
        return a if entry.get("zernioPostId") and targets_of(a) else None
    return a if targets_of(a) else None


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
                actions.append((vid, "sync", {"asset": asset}))
            elif (dist_state(e) == "failed"
                  and (e.get("distribution") or {}).get("transient")
                  and attempts(e) < MAX_ATTEMPTS):
                # A transient failure (e.g. "TikTok direct posting is at
                # capacity right now") clears on its own - re-publish on the
                # hourly run. MAX_ATTEMPTS hourly retries span the "few
                # hours" Zernio says capacity takes to free up.
                actions.append((vid, "retry", {"asset": asset}))
            elif (dist_state(e) == "failed"
                  and "transient" not in (e.get("distribution") or {})):
                # A failure recorded before transient/permanent was
                # classified (older code, or a pre-classification write).
                # Re-check via get_post to capture the real error and set
                # the transient flag, so the next run can retry it if it is
                # just capacity/rate limit.
                actions.append((vid, "sync", {"asset": asset}))
        elif sent and not e.get("published_at") and status != APPROVED:
            # approval was withdrawn after the send (back to editing /
            # rejected) - take it off the distributor's calendar too
            if dist_state(e) != "canceled":
                actions.append((vid, "cancel", {"asset": asset}))
    return actions


def upload_output(entry: dict, work_dir: Path) -> dict:
    """Release asset -> local bytes -> Zernio storage -> media dict.

    isAi: False - these are real commercial app videos (curated footage,
    conventionally edited: trim/subtitles/outro), not AI-generated media,
    so Meta's AI self-disclosure flag does not apply. The characters'
    pipeline keeps the flag: there the imagery is fully synthetic."""
    from distributors import zernio
    url = output_url(entry)
    if not url:
        raise RuntimeError("entry has no output_asset")
    local = work_dir / f"{entry['video_id']}.mp4"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as r:
        local.write_bytes(r.read())
    hosted = zernio.upload_media(local, "video/mp4", local.name)
    return {"type": "video", "url": hosted, "mimeType": "video/mp4",
            "isAi": False}


def execute(actions, ledger, work_dir) -> tuple[dict, list]:
    """Run the network side. Returns (patches, inbox) where inbox is a
    list of distribution-inbox notice records for every failure surfaced
    this run.

    Per-entry driver selection: NEW sends go through the venture's
    current distributor (buffer for a migrated venture, zernio
    otherwise); an entry already sent stays with the service that holds
    its post (distribution.via) for sync/cancel, so Planty's pending
    Zernio posts keep working after her migration. Buffer holds are not
    failures: a full channel queue -> queue_wait, a not-yet-mirrored
    video -> media_wait; both retry on the next run without burning
    attempts."""
    by_id = {e.get("video_id"): e for e in ledger}
    patches = {}
    inbox = []
    for vid, verb, ctx in actions:
        e = by_id[vid]
        asset = ctx["asset"]
        cur_drv = buffer_wiring.distributor_of(asset)
        sent_drv = driver_name_for(e, asset)
        caption = e.get("caption") or ""

        def _key_missing(drv_name):
            if drv_name == "buffer":
                if os.environ.get(buffer_wiring.key_env_of(asset), ""):
                    return None
                return buffer_wiring.key_env_of(asset)
            return None if key_of(asset) else key_env_of(asset)

        needed = {sent_drv} if verb in ("cancel", "sync") else {cur_drv, sent_drv}
        missing = [env for env in (_key_missing(d) for d in needed) if env]
        if missing:
            print(f"::notice::[{vid}] no key for venture "
                  f"'{asset.get('id')}' (expected env {', '.join(missing)}) "
                  "- skipped")
            continue

        creates = verb in ("publish", "retry", "schedule", "reschedule")
        targets = targets_for(asset, e) if creates else []
        if creates and not targets:
            # every connected platform was unchecked on the calendar
            print(f"::notice::[{vid}] no platforms selected - skipped")
            continue
        platforms = sorted({t["platform"] for t in targets}) if creates else \
            ((e.get("distribution") or {}).get("platforms")
             or sorted({t["platform"] for t in connected_targets_of(asset)}))

        def _fail_patch(msg, transient, bump=True, via=None):
            dist = {**(e.get("distribution") or {}), "via": via or cur_drv,
                    "platforms": platforms, "state": "failed",
                    "transient": bool(transient), "error": str(msg)[:300]}
            if bump:
                dist["attempts"] = attempts(e) + 1
            return {"distribution": dist}

        def _hold_patch(state, reason):
            return {"distribution": {**(e.get("distribution") or {}),
                                     "via": cur_drv, "state": state,
                                     "reason": str(reason)[:200]}}

        def _media():
            if cur_drv == "buffer":
                return buffer_media(e)  # public Pages URL (may raise MediaWait)
            with key_ctx_for("zernio", asset):
                return upload_output(e, work_dir)

        def _cancel_old():
            if not e.get("zernioPostId"):
                return
            try:
                with key_ctx_for(sent_drv, asset):
                    distributors.get(sent_drv).cancel_post(e["zernioPostId"])
            except Exception as err:
                print(f"  [{vid}] cancel of old {sent_drv} post failed: {err}",
                      file=sys.stderr)

        try:
            if verb in ("publish", "retry"):
                # retry re-publishes a transiently-failed post: drop the
                # dead one first so we do not leave an orphan behind.
                if verb == "retry":
                    _cancel_old()
                media = _media()
                with key_ctx_for(cur_drv, asset):
                    res = distributors.get(cur_drv).publish_now(
                        targets, media, caption)
                published = res["status"] == "published" or bool(res["postUrl"])
                if published or res["status"] == "publishing":
                    patches[vid] = {
                        "zernioPostId": res["externalId"],
                        **({"zernioPostUrl": res["postUrl"]} if res["postUrl"] else {}),
                        **({"status": "published", "published_at": now_z(),
                            "published_to": sorted(set((e.get("published_to") or [])
                                                       + platforms))}
                           if published else {}),
                        "publishNow": None,
                        "distribution": {"via": cur_drv, "platforms": platforms,
                                         **({"urls": res["urls"]} if res.get("urls") else {}),
                                         "state": "published" if published
                                         else "publishing"},
                    }
                else:
                    msg = errors_text(res) or f"{cur_drv} status {res.get('status')}"
                    patch = _fail_patch(msg, zernio_inbox.is_transient(msg))
                    if res.get("externalId"):
                        # a post exists at the service - stop re-sending as a
                        # fresh publish; retries continue via the transient path
                        patch["zernioPostId"] = res["externalId"]
                        patch["publishNow"] = None
                    patches[vid] = patch
                    for x in (res.get("errors")
                              or [{"platform": "", "message": msg}]):
                        inbox.append(notice(e, asset, x.get("platform"),
                                            x.get("message")))
            elif verb in ("schedule", "reschedule"):
                if cur_drv == "buffer":
                    # Buffer Free: 10 pending posts per channel. Full ->
                    # hold, retry next run (a slot frees on every publish).
                    with key_ctx_for(cur_drv, asset):
                        full = distributors.get("buffer").check_queues(targets)
                    if full:
                        patches[vid] = _hold_patch(
                            "queue_wait", "queue full: " + ", ".join(full))
                        print(f"[{vid}] held - Buffer queue full on "
                              f"{', '.join(full)}; retrying next run")
                        continue
                if verb == "reschedule":
                    _cancel_old()
                media = _media()
                with key_ctx_for(cur_drv, asset):
                    res = distributors.get(cur_drv).schedule_post(
                        targets, media, caption, ctx["when"])
                patches[vid] = {
                    "zernioPostId": res["externalId"],
                    "distribution": {"via": cur_drv, "state": "scheduled",
                                     "platforms": platforms,
                                     "scheduledFor": ctx["when"]},
                }
            elif verb == "cancel":
                with key_ctx_for(sent_drv, asset):
                    distributors.get(sent_drv).cancel_post(e["zernioPostId"])
                patches[vid] = {"distribution": {**(e.get("distribution") or {}),
                                                 "state": "canceled"}}
            elif verb == "sync":
                # Buffer quota frugality: a scheduled post cannot have
                # published before its slot - skip the status call until
                # ~5 minutes before scheduledFor.
                if sent_drv == "buffer":
                    sched = (e.get("distribution") or {}).get("scheduledFor")
                    soon = (datetime.datetime.now(datetime.timezone.utc)
                            + datetime.timedelta(minutes=5)
                            ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    if sched and sched > soon:
                        continue
                with key_ctx_for(sent_drv, asset):
                    res = distributors.get(sent_drv).get_post(e["zernioPostId"])
                if res["status"] == "published":
                    patches[vid] = {
                        "status": "published", "published_at": now_z(),
                        "published_to": sorted(set((e.get("published_to") or [])
                                                   + platforms)),
                        **({"zernioPostUrl": res["postUrl"]} if res["postUrl"] else {}),
                        "distribution": {**(e.get("distribution") or {}),
                                         **({"urls": res["urls"]} if res.get("urls") else {}),
                                         "state": "published"}}
                elif res["status"] in ("failed", "partial"):
                    msg = errors_text(res) or f"{sent_drv} status {res['status']}"
                    # detection, not our send - don't bump attempts here; the
                    # transient flag lets plan_actions schedule a retry.
                    patches[vid] = _fail_patch(
                        msg, zernio_inbox.is_transient(msg), bump=False,
                        via=sent_drv)
                    for x in (res.get("errors")
                              or [{"platform": "", "message": msg}]):
                        inbox.append(notice(e, asset, x.get("platform"),
                                            x.get("message")))
            print(f"[{vid}] {verb}: ok ({cur_drv if creates else sent_drv})")
        except MediaWait as err:
            patches[vid] = _hold_patch("media_wait",
                                       f"waiting for Pages mirror: {err}")
            print(f"[{vid}] held - media not mirrored yet; retrying next run")
        except QueueFullError as err:
            patches[vid] = _hold_patch("queue_wait", err)
            print(f"[{vid}] held - {err}; retrying next run")
        except Exception as err:
            print(f"[{vid}] {verb} FAILED: {err}", file=sys.stderr)
            patches[vid] = _fail_patch(str(err), zernio_inbox.is_transient(str(err)))
            inbox.append(notice(e, asset, "", str(err)))
    return patches, inbox


def discover_wirings() -> dict:
    """Per-venture auto-wiring: each venture has its own Zernio account
    holding that venture's connected social accounts. For every asset
    whose key is configured but whose targets are empty, record ALL its
    active connected accounts (any platform) as publish targets. Returns
    {asset_id: [{platform, accountId, username}]}."""
    from distributors import zernio
    wirings = {}
    for asset in load_json(ASSETS, []):
        if targets_of(asset):
            continue
        key = key_of(asset)
        if not key:
            continue
        try:
            with with_key(key):
                accts = [a for a in zernio.list_accounts()
                         if a.get("isActive", True) and a.get("_id")
                         and a.get("platform")]
        except RuntimeError as e:
            print(f"::warning::listing accounts for '{asset.get('id')}' "
                  f"failed: {e}", file=sys.stderr)
            continue
        if accts:
            wirings[str(asset["id"])] = [
                {"platform": a["platform"], "accountId": str(a["_id"]),
                 "username": a.get("username", "")} for a in accts]
            print(f"auto-wire {asset['id']} -> "
                  + ", ".join(f"{a['platform']}:{a.get('username')}" for a in accts))
        else:
            print(f"venture '{asset.get('id')}': no active connected accounts "
                  "in its Zernio yet")
    return wirings


def autowire_assets() -> None:
    wirings = discover_wirings()
    if not wirings:
        return
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com")
    for attempt in range(3):
        git("fetch", "origin", "main")
        git("reset", "--hard", "origin/main")
        assets = load_json(ASSETS, [])
        changed = False
        for a in assets:
            tg = wirings.get(str(a.get("id")))
            if tg and not targets_of(a):
                a["zernioTargets"] = tg
                # keep the legacy single field warm for older UI reads
                a.setdefault("zernioAccountId", tg[0]["accountId"])
                changed = True
        if not changed:
            return
        ASSETS.write_text(json.dumps(assets, indent=1) + "\n", encoding="utf-8")
        git("add", "assets.json")
        git("commit", "-m",
            f"Apps: wire Zernio targets for {', '.join(sorted(wirings))}")
        if git("push", "origin", "main", check=False).returncode == 0:
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
    any_zernio = os.environ.get("ZERNIO_APPS_API_KEY") or \
        any(k.startswith("ZERNIO_KEY_") and v
            for k, v in os.environ.items())
    any_buffer = buffer_wiring.any_buffer_key()
    if not any_zernio and not any_buffer:
        print("::notice::no venture distributor key is configured "
              "(ZERNIO_KEY_<VENTURE> / BUFFER_TOKEN_<VENTURE>) - app "
              "distribution skipped")
        return 0
    if any_zernio:
        autowire_assets()
    if any_buffer:
        buffer_wiring.autowire_channels(ASSETS)
    assets = load_json(ASSETS, [])
    if not any(connected_targets_of(a) or targets_of(a) for a in assets):
        print("no venture has connected distribution targets yet - "
              "nothing to distribute")
        return 0
    ledger = load_json(LEDGER, [])
    actions = plan_actions(ledger, assets)
    if not actions:
        print("nothing to distribute")
        return 0
    work_dir = Path("out/distribute-apps")
    work_dir.mkdir(parents=True, exist_ok=True)
    patches, inbox = execute(actions, ledger, work_dir)
    rc = 0
    if patches:
        rc = commit_ledger(patches, "Apps: distribution sync (zernio)")
    # Surface every failure in the "Zernio Inbox" tab (committed separately
    # so the ledger push above is never blocked by the feed write).
    if inbox:
        zernio_inbox.append(inbox)
    failures = [v for v, p in patches.items()
                if (p.get("distribution") or {}).get("error")]
    if failures:
        print(f"::warning::{len(failures)} video(s) failed distribution: "
              + ", ".join(failures))
    return rc


if __name__ == "__main__":
    sys.exit(main())
