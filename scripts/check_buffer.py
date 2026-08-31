#!/usr/bin/env python3
"""Buffer connection check (doctor) for one brand.

Verifies the brand's BUFFER_TOKEN_<BRAND> secret works, prints the
account's organization and connected channels (platform/name - public
information) with each channel's queue depth, and whether the brand's
wiring matches. A successful check also runs the same channel auto-wire
the distributor does, so a token paste alone completes the setup.

Brands live in assets.json (app ventures - Planty first) or
data/aimodels/roster.json (characters); lookup is by id OR name,
case-insensitive, so `planty` finds the venture whose legacy id is
'sample'.

Usage (runs from .github/workflows/buffer-test.yml, or locally):
    python scripts/check_buffer.py <brand-id-or-name>

Exits non-zero when the key is missing or rejected - this check exists
to prove the key works before the first real post.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buffer_wiring  # noqa: E402
from distributors import buffer  # noqa: E402


def main() -> int:
    buffer_wiring.load_secrets_context()
    ident = sys.argv[1] if len(sys.argv) > 1 else "planty"
    brand, path = buffer_wiring.find_brand(ident)
    if not brand:
        print(f"::error::brand '{ident}' is not in assets.json or "
              "roster.json", file=sys.stderr)
        return 1
    env = buffer_wiring.key_env_of(brand)
    key = os.environ.get(env, "")
    if not key:
        print(f"::error::secret {env} is not set (Settings > Secrets and "
              "variables > Actions)", file=sys.stderr)
        return 1
    try:
        with buffer_wiring.with_key(key):
            org = buffer.organization_id()
            channels = buffer.list_channels()
            print(f"key OK - organization {org}, "
                  f"{len(channels)} connected channel(s):")
            for c in channels:
                depth = buffer.pending_count(c["id"])
                print(f"  {c.get('service')}: {c.get('name')}"
                      f" - queue {depth}/{buffer.QUEUE_LIMIT}"
                      f"{' (PAUSED)' if c.get('isQueuePaused') else ''}")
    except RuntimeError as e:
        print(f"::error::Buffer rejected the key or the call failed: {e}",
              file=sys.stderr)
        return 1
    if not channels:
        print("::warning::no channels connected in this Buffer account yet "
              "- connect Facebook/Instagram/TikTok in the Buffer dashboard")
        return 0
    if buffer_wiring.distributor_of(brand) != "buffer":
        print(f"::warning::brand '{brand.get('id')}' ({path.name}) has no "
              'distributor: "buffer" yet - it still publishes through Zernio')
    # Full re-sync every doctor run: picks up channels connected to the
    # Buffer account AFTER the initial wiring (e.g. adding FB+IG to a
    # brand that started TikTok-only). The hourly runner only wires
    # empty brands (quota frugality) - the doctor is the refresh button.
    buffer_wiring.autowire_channels(path, refresh=True)
    print("doctor: all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
