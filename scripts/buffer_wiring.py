#!/usr/bin/env python3
"""Shared Buffer brand wiring, used by BOTH runners and the owner
scripts. A "brand" is any entry that can publish through Buffer:

- an app venture in assets.json   (Planty - the first Buffer brand)
- a character/brand in data/aimodels/roster.json

Both files are JSON lists of {id, name?, ...}; a brand opts into Buffer
with distributor: "buffer" and carries:
  bufferKeyEnv    env var holding its API key (default
                  BUFFER_TOKEN_<ID>); one Buffer account = one key = one
                  GitHub secret per brand
  bufferChannels  {platform: channelId} - auto-wired from the brand's
                  Buffer account on the first run with a working token
"""
import json
import os
import re
import sys
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_daily import REPO_ROOT, git, load_json  # noqa: E402

ROSTER = REPO_ROOT / "data" / "aimodels" / "roster.json"
ASSETS = REPO_ROOT / "assets.json"


def distributor_of(brand: dict) -> str:
    """zernio (the default and fallback) unless the brand opted into
    buffer. Switching a brand back = deleting the field."""
    return (brand or {}).get("distributor") or "zernio"


def key_env_of(brand: dict) -> str:
    return brand.get("bufferKeyEnv") or \
        "BUFFER_TOKEN_" + re.sub(r"[^A-Za-z0-9]", "_", str(brand.get("id"))).upper()


def targets_of(brand: dict) -> list[dict]:
    """[{platform, channelId}] from the wired bufferChannels."""
    ch = brand.get("bufferChannels") or {}
    return [{"platform": p, "channelId": cid} for p, cid in ch.items() if cid]


@contextmanager
def with_key(key: str):
    """The buffer driver reads BUFFER_ACCESS_TOKEN; swap the brand's key
    in for the duration of its API calls (single-threaded runners)."""
    prev = os.environ.get("BUFFER_ACCESS_TOKEN")
    os.environ["BUFFER_ACCESS_TOKEN"] = key
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("BUFFER_ACCESS_TOKEN", None)
        else:
            os.environ["BUFFER_ACCESS_TOKEN"] = prev


def key_ctx_of(brand: dict):
    """Context manager for running driver calls as this brand: swaps its
    Buffer key in when it is a buffer brand, no-op otherwise."""
    if distributor_of(brand) == "buffer":
        return with_key(os.environ.get(key_env_of(brand), ""))
    return nullcontext()


def ready(brand: dict) -> bool:
    """A buffer brand can publish when its token secret exists AND its
    channels are wired. It never silently falls back to Zernio - a
    migrated brand cannot surprise-publish through the old account."""
    return bool(os.environ.get(key_env_of(brand))) and bool(targets_of(brand))


def find_brand(ident: str) -> tuple[dict | None, Path | None]:
    """Locate a brand by id or (case-insensitive) name across both
    registries; returns (brand, file) - assets.json first so 'planty'
    finds the venture whose legacy id is 'sample'."""
    want = str(ident).strip().lower()
    for path in (ASSETS, ROSTER):
        for b in load_json(path, []):
            if want in (str(b.get("id", "")).lower(),
                        str(b.get("name", "")).lower()):
                return b, path
    return None, None


def autowire_channels(path: Path, quiet_missing: bool = False) -> None:
    """For every buffer brand in `path` whose token exists but whose
    bufferChannels are empty, list its Buffer account's channels and
    commit {service: channelId} back to the file (race-safe fetch/reset/
    push, the repo's standard pattern). The owner only pastes the token;
    channel-id hunting is automated. ~2 API calls per brand, once ever."""
    from distributors import buffer as buffer_drv
    wirings = {}
    for brand in load_json(path, []):
        if distributor_of(brand) != "buffer" or targets_of(brand):
            continue
        key = os.environ.get(key_env_of(brand), "")
        if not key:
            if not quiet_missing:
                print(f"::notice::brand '{brand.get('id')}' is set to Buffer "
                      f"but secret {key_env_of(brand)} is not configured - skipped")
            continue
        try:
            with with_key(key):
                channels = buffer_drv.list_channels()
        except RuntimeError as e:
            print(f"::warning::listing Buffer channels for "
                  f"'{brand.get('id')}' failed: {e}", file=sys.stderr)
            continue
        wired = {c["service"]: str(c["id"]) for c in channels
                 if c.get("id") and c.get("service")}
        if wired:
            wirings[str(brand["id"])] = wired
            print(f"auto-wire {brand['id']} -> "
                  + ", ".join(f"{c['service']}:{c.get('name', '')}"
                              for c in channels
                              if c.get("id") and c.get("service")))
        else:
            print(f"brand '{brand.get('id')}': no channels connected in its "
                  "Buffer account yet")
    if not wirings:
        return
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com")
    for attempt in range(3):
        git("fetch", "origin", "main")
        git("reset", "--hard", "origin/main")
        fresh = load_json(path, [])
        changed = False
        for b in fresh:
            wired = wirings.get(str(b.get("id")))
            if wired and not (b.get("bufferChannels") or {}):
                b["bufferChannels"] = wired
                changed = True
        if not changed:
            return
        path.write_text(json.dumps(fresh, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
        git("add", str(path.relative_to(REPO_ROOT)))
        git("commit", "-m",
            f"Buffer: wire channels for {', '.join(sorted(wirings))}")
        if git("push", "origin", "main", check=False).returncode == 0:
            return
        time.sleep(2 * (attempt + 1))
    print(f"::warning::could not push {path.name} Buffer auto-wire",
          file=sys.stderr)


def any_buffer_key() -> bool:
    return any(k.startswith("BUFFER_TOKEN_") and v
               for k, v in os.environ.items())


def load_secrets_context() -> None:
    """Adding a brand must not require editing workflow files, so the
    workflows pass ALL repo secrets as one JSON blob (the documented
    GitHub Actions pattern for dynamically-named secrets:
    SECRETS_CONTEXT: toJSON(secrets)). Only distributor keys -
    BUFFER_TOKEN_* / ZERNIO_KEY_* - are lifted into the environment;
    values are never printed and GitHub masks them in logs anyway.
    Explicit env lines still win (we never overwrite an existing var)."""
    raw = os.environ.get("SECRETS_CONTEXT", "")
    if not raw:
        return
    try:
        secrets = json.loads(raw)
    except Exception:
        print("::warning::SECRETS_CONTEXT is not valid JSON - ignored",
              file=sys.stderr)
        return
    loaded = []
    for k, v in secrets.items():
        if (re.fullmatch(r"(BUFFER_TOKEN|ZERNIO_KEY)_[A-Za-z0-9_]+", k)
                and v and not os.environ.get(k)):
            os.environ[k] = v
            loaded.append(k)
    if loaded:
        print("distributor keys from secrets context: "
              + ", ".join(sorted(loaded)))
