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
- A single video mediaItem publishes as a Reel; platformSpecificData
  {contentType: "story"} makes a Story; images default to feed posts.
- platformSpecificData.isAiGenerated: true labels the post as AI-generated
  media (Meta self-disclosure) - always set: every asset here is synthetic
  and the characters' bios say so.
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


def _platform_entry(account_id: str, media: dict) -> dict:
    psd = {"isAiGenerated": True}
    if media.get("story"):
        psd["contentType"] = "story"
    return {"platform": "instagram", "accountId": account_id,
            "platformSpecificData": psd}


def _result(resp: dict) -> dict:
    post = resp.get("post") or {}
    platforms = post.get("platforms") or [{}]
    url = None
    for p in platforms:
        if p.get("platformPostUrl"):
            url = p["platformPostUrl"]
            break
        if p.get("errorMessage"):
            print(f"  zernio platform error: {p['errorMessage'][:200]}",
                  file=sys.stderr)
    return {"externalId": post.get("_id"),
            "status": post.get("status"),
            "postUrl": url}


def schedule_post(account_id: str, media: dict, caption: str,
                  scheduled_at: str) -> dict:
    return _result(_api("/posts", {
        "content": caption,
        "mediaItems": [_media_item(media)],
        "platforms": [_platform_entry(account_id, media)],
        "scheduledFor": scheduled_at,
    }))


def publish_now(account_id: str, media: dict, caption: str) -> dict:
    return _result(_api("/posts", {
        "content": caption,
        "mediaItems": [_media_item(media)],
        "platforms": [_platform_entry(account_id, media)],
        "publishNow": True,
    }))


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
