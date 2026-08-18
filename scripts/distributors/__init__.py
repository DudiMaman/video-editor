"""Distributor abstraction for the AI-models project.

A distributor pushes an owner-approved item to a social platform. Every
driver module exposes the same contract (all times are UTC ISO 8601 with
a trailing Z; `media` is {"type": "image"|"video", "url": ..., optional
"mimeType", optional "cover" (video poster URL), optional "story": True}):

  schedule_post(account_id, media, caption, scheduled_at) -> result
  publish_now(account_id, media, caption)                 -> result
  get_post(external_id)                                   -> result
  cancel_post(external_id)                                -> None

`result` is {"externalId": str, "status": str, "postUrl": str|None}.
Drivers raise RuntimeError with a readable message on API failure.

The UI never talks to a distributor - publishing happens in GitHub
Actions (scripts/distribute_aimodels.py) so API keys stay server-side.
A future driver (e.g. the Instagram Graph API) slots in here without
touching the tab.
"""
from . import zernio

DRIVERS = {"zernio": zernio}


def get(name: str):
    try:
        return DRIVERS[name]
    except KeyError:
        raise RuntimeError(f"unknown distributor: {name}") from None
