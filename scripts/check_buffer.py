#!/usr/bin/env python3
"""Buffer connection check (doctor) for one brand.

Verifies the brand's BUFFER_TOKEN_<BRAND> secret works, prints the
account's organization and connected channels (platform/name - public
information), the per-channel queue depth, and whether the brand's
roster.json wiring matches. Read-only except that a successful check
also runs the same channel auto-wire the distributor does, so a token
paste alone completes the setup.

Usage (runs from .github/workflows/buffer-test.yml, or locally):
    python scripts/check_buffer.py <brand-id>
The brand's token is read from the env var named by the brand's
bufferKeyEnv (default BUFFER_TOKEN_<BRAND-ID>).

Exits non-zero when the key is missing or rejected - this check exists
to prove the key works before the first real post.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_daily import load_json  # noqa: E402
import distribute_aimodels as run  # noqa: E402
from distributors import buffer  # noqa: E402
import os  # noqa: E402


def main() -> int:
    brand_id = sys.argv[1] if len(sys.argv) > 1 else "ruby"
    roster = load_json(run.ROSTER, [])
    char = next((c for c in roster if str(c.get("id")) == brand_id), None)
    if not char:
        print(f"::error::brand '{brand_id}' is not in roster.json",
              file=sys.stderr)
        return 1
    env = run.buffer_key_env_of(char)
    key = os.environ.get(env, "")
    if not key:
        print(f"::error::secret {env} is not set (Settings > Secrets and "
              "variables > Actions)", file=sys.stderr)
        return 1
    try:
        with run.with_buffer_key(key):
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
    if run.distributor_of(char) != "buffer":
        print(f"::warning::brand '{brand_id}' has no distributor: \"buffer\" "
              "in roster.json yet - it still publishes through Zernio")
    if not run.buffer_targets_of(char):
        print("channels not wired in roster yet - auto-wiring now")
        run.autowire_buffer_channels(roster)
    else:
        print(f"roster wiring: {char.get('bufferChannels')}")
    print("doctor: all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
