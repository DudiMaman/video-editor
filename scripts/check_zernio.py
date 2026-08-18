#!/usr/bin/env python3
"""Zernio connection check + roster wiring.

Verifies ZERNIO_API_KEY works, lists the connected social accounts
(platform/username only - public information), and fills in
roster.json zernioAccountId for characters it can match:

1. A connected Instagram account whose username equals a character's
   handle (case-insensitive, without the @) wires that character.
2. If exactly one Instagram account is connected and ruby is still
   unwired, it is hers - the rollout plan connects Ruby first and the
   free tier holds two accounts.

Runs from .github/workflows/check-zernio.yml (fires on changes to that
file and on manual dispatch). Exits with an error when the key is
missing or rejected - this check exists to prove the key works.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_daily import REPO_ROOT, git  # noqa: E402
from distributors import zernio  # noqa: E402

ROSTER = REPO_ROOT / "data" / "aimodels" / "roster.json"


def wire(roster: list, ig_accounts: list) -> list[str]:
    """Fill empty zernioAccountId fields; returns the wired char ids."""
    by_username = {str(a.get("username", "")).lstrip("@").lower(): a
                   for a in ig_accounts}
    wired = []
    for char in roster:
        if char.get("zernioAccountId"):
            continue
        handle = str(char.get("handle", "")).lstrip("@").lower()
        acc = by_username.get(handle)
        if acc:
            char["zernioAccountId"] = str(acc["_id"])
            wired.append(char["id"])
    if not wired and len(ig_accounts) == 1:
        ruby = next((c for c in roster if c["id"] == "ruby"
                     and not c.get("zernioAccountId")), None)
        if ruby:
            ruby["zernioAccountId"] = str(ig_accounts[0]["_id"])
            wired.append("ruby")
    return wired


def main() -> int:
    if not os.environ.get("ZERNIO_API_KEY"):
        print("::error::ZERNIO_API_KEY secret is not set (Settings > "
              "Secrets and variables > Actions)", file=sys.stderr)
        return 1
    try:
        accounts = zernio.list_accounts()
    except RuntimeError as e:
        print(f"::error::Zernio rejected the key or the call failed: {e}",
              file=sys.stderr)
        return 1
    print(f"key OK - {len(accounts)} connected account(s):")
    for a in accounts:
        print(f"  {a.get('platform')}: {a.get('username')}"
              f"{' (inactive)' if not a.get('isActive', True) else ''}")
    ig = [a for a in accounts
          if a.get("platform") == "instagram" and a.get("isActive", True)]
    if not ig:
        print("::warning::no active Instagram account is connected in "
              "Zernio yet - connect one in the Zernio dashboard")
        return 0

    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com")
    for attempt in range(3):
        git("fetch", "origin", "main")
        git("reset", "--hard", "origin/main")
        roster = json.loads(ROSTER.read_text(encoding="utf-8"))
        wired = wire(roster, ig)
        if not wired:
            print("nothing to wire (all matching characters already have "
                  "a zernioAccountId)")
            return 0
        ROSTER.write_text(json.dumps(roster, ensure_ascii=False, indent=1)
                          + "\n", encoding="utf-8")
        git("add", "data/aimodels/roster.json")
        git("commit", "-m",
            f"AI models: wire Zernio account for {', '.join(wired)}")
        push = git("push", "origin", "main", check=False)
        if push.returncode == 0:
            print(f"wired: {', '.join(wired)}")
            return 0
        time.sleep(2 * (attempt + 1))
    print("::error::could not push roster.json", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
