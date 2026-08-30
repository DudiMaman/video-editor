"""Zernio driver - implemented against https://zernio.com/openapi.yaml
(fetched 2026-08-18) and docs.zernio.com/platforms/instagram.

API facts this code relies on:
- Base URL https://zernio.com/api, bearer auth: Authorization: Bearer <key>.
- One endpoint for everything: POST /v1/posts with content, mediaItems
  [{type, url, mimeType, instagramThumbnail}], platforms [{platform,
  accountId, platformSpecificData}], and either publishNow: true or
  scheduledFor (ISO 8601; we always send UTC with a trailing Z, which is
  unambiguous, so the separate `timezone` field is not needed).
- The created post id is post._id; post.status is one of draft/scheduled/
  publishing/published/failed/partial; per-platform entries carry
  platformPostUrl once published.
- Multi-platform: publish_now/schedule_post take `targets` - a bare
  account-id string (legacy: Instagram) or a list of {platform,
  accountId}. All targets go out in ONE request (platforms[]), so a
  single post._id stays the idempotency key; per-platform URLs come back
  in the result's `urls`. Each platform gets the correct
  platformSpecificData: Instagram (story/reel + isAiGenerated), Facebook
  (video -> Reel), TikTok (privacyLevel from the account's creator-info,
  comment/duet/stitch, disclosure flags), YouTube (title, visibility,
  synthetic-media flag). A single video otherwise publishes as a Reel.
- platformSpecificData.isAiGenerated: true labels the post as AI-generated
  media (Meta self-disclosure). The flag describes the MEDIA and the
  caller opts IN per item via media["isAi"] (default: no label). The
  AI-characters pipeline sets it explicitly - its imagery is fully
  synthetic; the apps' videos are real edited footage. Any future
  pipeline that publishes AI-generated media MUST set isAi: True too -
  the flag follows the media, not the project.
- DELETE /v1/posts/{postId} cancels anything not yet published (400 once
  published).
- Media URLs must be publicly accessible AND served with a real
  Content-Type - the caller is responsible for passing such URLs (release
  assets are application/octet-stream and will fail).

The API key comes from the ZERNIO_API_KEY environment variable.
"""
import json
import os
import sys
import urllib.error
import urllib.request

API_BASE = "https://zernio.com/api/v1"
USER_AGENT = "video-editor-distribute/1.0 (+https://github.com/DudiMaman/video-editor)"


def _api(path: str, payload: dict | None = None, method: str | None = None) -> dict:
    key = os.environ.get("ZERNIO_API_KEY", "")
    if not key:
        raise RuntimeError("ZERNIO_API_KEY is not set")
    req = urllib.request.Request(
        API_BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "User-Agent": USER_AGENT},
        method=method or ("POST" if payload is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read().decode()
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:400]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code} from Zernio: {detail or e.reason}") from None


def _media_item(media: dict) -> dict:
    item = {"type": media["type"], "url": media["url"]}
    if media.get("mimeType"):
        item["mimeType"] = media["mimeType"]
    if media["type"] == "video" and media.get("cover"):
        item["instagramThumbnail"] = media["cover"]
    return item


_privacy_cache = {}


def tiktok_privacy(account_id: str) -> str:
    """The most public privacy level the connected TikTok account allows.
    TikTok requires privacyLevel to be one of the values its creator-info
    API returns for that account (unaudited apps are forced to SELF_ONLY);
    sending an unlisted value errors. Cached per account per run."""
    if account_id in _privacy_cache:
        return _privacy_cache[account_id]
    val = "PUBLIC_TO_EVERYONE"
    try:
        info = _api(f"/accounts/{account_id}/tiktok/creator-info")
        levels = [x.get("value") for x in (info.get("privacyLevels") or [])
                  if x.get("value")]
        if levels:
            val = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in levels else levels[0]
    except Exception as e:
        print(f"  tiktok creator-info failed ({str(e)[:120]}), "
              "defaulting privacy to PUBLIC_TO_EVERYONE", file=sys.stderr)
    _privacy_cache[account_id] = val
    return val


def _normalize_targets(targets) -> list[dict]:
    """Accept a bare account-id string (legacy: Instagram) or a list of
    {platform, accountId} dicts."""
    if isinstance(targets, str):
        return [{"platform": "instagram", "accountId": targets}]
    out = []
    for t in targets or []:
        if t.get("accountId") and t.get("platform"):
            out.append({"platform": t["platform"], "accountId": t["accountId"]})
    return out


def _platform_entry(target: dict, media: dict) -> dict:
    """Build one platforms[] entry with the platform-correct
    platformSpecificData. `media` carries type/isAi/story/cover/title."""
    platform = target["platform"]
    is_video = media.get("type") == "video"
    is_ai = bool(media.get("isAi"))
    psd = {}
    if platform == "instagram":
        if is_ai:
            psd["isAiGenerated"] = True
        if media.get("story"):
            psd["contentType"] = "story"
        # a single video otherwise publishes as a Reel automatically
    elif platform == "facebook":
        # vertical marketing video -> Facebook Reel; image/story optional
        if media.get("story"):
            psd["contentType"] = "story"
        elif is_video:
            psd["contentType"] = "reel"
    elif platform == "tiktok":
        psd.update({
            "privacyLevel": tiktok_privacy(target["accountId"]),
            "allowComment": True,
            "allowDuet": True,
            "allowStitch": True,
            "commercialContentType": "none",
            "contentPreviewConfirmed": True,
            "expressConsentGiven": True,
            "videoMadeWithAi": is_ai,
        })
    elif platform == "youtube":
        psd["visibility"] = "public"
        psd["containsSyntheticMedia"] = is_ai
        if media.get("title"):
            psd["title"] = media["title"][:100]
    else:
        # any other platform Zernio supports - post with defaults
        pass
    return {"platform": platform, "accountId": target["accountId"],
            "platformSpecificData": psd}


def _build(targets, media: dict, caption: str) -> dict:
    entries = [_platform_entry(t, media) for t in _normalize_targets(targets)]
    if not entries:
        raise RuntimeError("no publish targets")
    payload = {"content": caption, "mediaItems": [_media_item(media)],
               "platforms": entries}
    # YouTube needs a title; default to the first non-empty caption line.
    if any(e["platform"] == "youtube" for e in entries) and not media.get("title"):
        first = next((ln.strip() for ln in str(caption).splitlines() if ln.strip()), "")
        if first:
            payload["title"] = first[:100]
    return payload


def _result(resp: dict) -> dict:
    post = resp.get("post") or {}
    platforms = post.get("platforms") or [{}]
    url = None
    urls = {}
    errors = []  # [{platform, message}] - the real per-platform failure text
    for p in platforms:
        if p.get("platformPostUrl"):
            urls[p.get("platform", "?")] = p["platformPostUrl"]
            url = url or p["platformPostUrl"]
        msg = p.get("errorMessage") or p.get("error")
        if msg:
            errors.append({"platform": p.get("platform", "?"),
                           "message": str(msg)[:400]})
            print(f"  zernio {p.get('platform','?')} error: "
                  f"{str(msg)[:200]}", file=sys.stderr)
    # A post-level error (no per-platform breakdown) still needs surfacing.
    top = post.get("errorMessage") or post.get("error") or resp.get("error")
    if top and not errors:
        errors.append({"platform": "", "message": str(top)[:400]})
    return {"externalId": post.get("_id"),
            "status": post.get("status"),
            "postUrl": url, "urls": urls, "errors": errors}


def schedule_post(targets, media: dict, caption: str, scheduled_at: str) -> dict:
    return _result(_api("/posts", {**_build(targets, media, caption),
                                    "scheduledFor": scheduled_at}))


def publish_now(targets, media: dict, caption: str) -> dict:
    return _result(_api("/posts", {**_build(targets, media, caption),
                                   "publishNow": True}))


def get_post(external_id: str) -> dict:
    return _result(_api(f"/posts/{external_id}"))


def cancel_post(external_id: str) -> None:
    _api(f"/posts/{external_id}", method="DELETE")


def list_accounts() -> list[dict]:
    """Connected accounts - useful for finding a character's accountId
    (the SocialAccount `_id` field) when wiring roster.json."""
    return (_api("/accounts") or {}).get("accounts") or []


def upload_media(path, content_type: str, filename: str | None = None) -> str:
    """Host a local media file on Zernio's own storage and return its
    public URL: POST /v1/media/presign -> PUT the bytes to uploadUrl ->
    use publicUrl in mediaItems. This sidesteps the content-type law
    entirely (release assets serve application/octet-stream, which
    Zernio's URL fetcher rejects) and carries video sizes that would be
    unwieldy to mirror elsewhere."""
    from pathlib import Path as _P
    data = _P(path).read_bytes()
    name = filename or _P(path).name
    pre = _api("/media/presign", {"filename": name,
                                  "contentType": content_type,
                                  "size": len(data)})
    req = urllib.request.Request(
        pre["uploadUrl"], data=data, method="PUT",
        headers={"Content-Type": content_type, "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=600) as r:
        if r.status not in (200, 201, 204):
            raise RuntimeError(f"media upload failed: HTTP {r.status}")
    return pre["publicUrl"]
