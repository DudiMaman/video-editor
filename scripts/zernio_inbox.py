#!/usr/bin/env python3
"""Shared append-only feed of Zernio distribution problems, surfaced in
the "Zernio Inbox" tab (frontend/src/components/ZernioInboxTab.jsx).

Both distributors write here - distribute_apps.py (per-venture accounts:
Planty and future apps) and distribute_aimodels.py (the AI characters) -
so every account's errors land in one place the owner actually opens,
instead of only arriving as a Zernio e-mail. The feed mirrors what the
GET /v1/posts/{id} error details and the failure e-mails carry.

Record shape (id + ts filled in here):
  id             stable dedupe key (sha1 of postId|platform|level|message)
  ts             when we detected it (UTC, ...Z)
  source         "apps" | "aimodels"
  venture        venture/character id
  ventureName    human-readable venture/character name
  platform       "tiktok" | "instagram" | ... | "" (post-level)
  postId         Zernio post._id (the one to GET for full details)
  refId          our own id (ledger video_id / batch item id)
  level          "error" | "info"
  message        the failure text from Zernio
  remedy         "transient" when it is a capacity/rate-limit that will
                 clear on its own (the system auto-retries those), else ""
  contentPreview first ~120 chars of the caption
  scheduledFor   the UTC time the post was aimed at (when known)
  postUrl        public URL once the post is live (usually empty on error)

Writing is race-safe (fetch/reset/append/push with retries), the same
pattern as the ledger writer, and the feed is capped at MAX_RECORDS so it
never grows without bound.
"""
import datetime
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_daily import REPO_ROOT, git, load_json  # noqa: E402

FEED = REPO_ROOT / "data" / "zernio_inbox.json"
MAX_RECORDS = 500

# Substrings that mark a failure as transient - it will clear on its own,
# so the caller retries instead of giving up. Kept in sync with the tab's
# copy for the owner-facing remedy hint.
_TRANSIENT = ("capacity", "rate limit", "rate-limit", "try again",
              "temporarily", "timeout", "timed out", " 429", "http 429",
              "too many requests")


def now_z() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def is_transient(message: str) -> bool:
    m = str(message or "").lower()
    return any(s in m for s in _TRANSIENT)


def classify_remedy(message: str) -> str:
    return "transient" if is_transient(message) else ""


def errors_text(res: dict) -> str:
    """Flatten a driver result's per-platform errors into one line."""
    parts = []
    for x in res.get("errors") or []:
        p, m = x.get("platform"), x.get("message")
        parts.append(f"{p}: {m}" if p else str(m))
    return "; ".join(parts)


def _make_id(rec: dict) -> str:
    base = "|".join(str(rec.get(k, "")) for k in
                    ("postId", "platform", "level", "message"))
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def append(records: list) -> int:
    """Append notice records (dicts without id/ts) to the feed, skipping
    any whose dedupe id is already present. Returns the number added."""
    records = [r for r in (records or []) if r and r.get("message")]
    if not records:
        return 0
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com")
    for attempt in range(1, 6):
        git("fetch", "origin", "main")
        git("reset", "--hard", "origin/main")
        feed = load_json(FEED, [])
        have = {r.get("id") for r in feed}
        added = 0
        for r in records:
            rec = dict(r)
            rec.setdefault("ts", now_z())
            rec.setdefault("level", "error")
            rec["remedy"] = rec.get("remedy") or classify_remedy(
                rec.get("message", ""))
            rec["id"] = _make_id(rec)
            if rec["id"] in have:
                continue
            have.add(rec["id"])
            feed.append(rec)
            added += 1
        if not added:
            print("zernio inbox: nothing new to record")
            return 0
        feed = feed[-MAX_RECORDS:]
        FEED.parent.mkdir(parents=True, exist_ok=True)
        FEED.write_text(json.dumps(feed, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
        git("add", str(FEED.relative_to(REPO_ROOT)))
        git("commit", "-m", f"Zernio inbox: +{added} notice(s)")
        if git("push", "origin", "main", check=False).returncode == 0:
            print(f"zernio inbox: pushed {added} notice(s) on attempt {attempt}")
            return added
        time.sleep(2 * attempt)
    print("ERROR: could not push zernio_inbox.json", file=sys.stderr)
    return 0
