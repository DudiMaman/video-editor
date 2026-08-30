"""Buffer driver - implemented against the live Buffer GraphQL API docs
(developers.buffer.com, fetched 2026-08-30: guides/posts-and-scheduling,
guides/error-handling, guides/api-limits, reference.md).

API facts this code relies on:
- Single GraphQL endpoint: POST https://api.buffer.com with
  Authorization: Bearer <key>. One API key per Buffer account (Free plan:
  exactly 1 key). We run ONE BUFFER ACCOUNT PER BRAND, so the runner
  swaps each brand's key into BUFFER_ACCESS_TOKEN before calling here
  (same pattern as ZERNIO_API_KEY in the zernio driver).
- Rate limits on Free: 100 req/15min, 250 req/24h, 3,000 req/30 days.
  429 responses carry extensions.code RATE_LIMIT_EXCEEDED and a
  Retry-After header - _api retries with backoff on 429/5xx; every call
  is logged so quota use stays visible in the Action log.
- createPost(input: CreatePostInput!) creates ONE post on ONE channel:
  {text, channelId, schedulingType: automatic, mode: shareNow |
  customScheduled (+dueAt, ISO 8601 UTC), assets, metadata}. Multi-
  channel fan-out is therefore one createPost per channel; the driver
  returns every created post id and joins them with "," into externalId
  so the caller's single-id idempotency key keeps working.
- Media rides as PUBLIC URLS only (no byte upload):
  assets: [{image: {url}}] or [{video: {url, metadata:
  {thumbnailOffset: ms}}}]. Video assets MUST NOT set thumbnailUrl -
  the API rejects it; the thumbnail frame is picked via thumbnailOffset.
- Service-specific metadata (only for the network the channel belongs
  to): instagram {type: post|story|reel, shouldShareToFeed,
  isAiGenerated}, facebook {type: post|story|reel}, tiktok
  {isAiGenerated}. isAiGenerated follows the MEDIA (media["isAi"]),
  exactly like the zernio driver: AI-characters content sets it, real
  commercial footage does not.
- Mutations return a union: PostActionSuccess {post} |
  LimitReachedError | InvalidInputError | MutationError {message}.
  LimitReachedError is how a full queue answers (Free plan: 10 pending
  posts per channel) - surfaced as QueueFullError so the runner can
  HOLD the item and retry next run instead of failing it.
- post(input: {id}) -> {status: draft|error|needs_approval|scheduled|
  sending|sent, externalLink, error {message}}. posts(first, input:
  {organizationId, filter: {channelIds, status: [scheduled]}}) counts a
  channel's pending queue.
- deletePost(input: {id}) cancels anything not yet sent.

The API key comes from the BUFFER_ACCESS_TOKEN environment variable.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

API_URL = "https://api.buffer.com"
USER_AGENT = "video-editor-distribute/1.0 (+https://github.com/DudiMaman/video-editor)"

# Free-plan queue cap: up to 10 scheduled posts waiting per channel.
QUEUE_LIMIT = 10

_RETRIES = 3


class QueueFullError(RuntimeError):
    """The channel's Buffer queue is full (LimitReachedError). The post
    was NOT created; the caller should hold it and retry on a later run
    - a slot frees up whenever a queued post publishes."""


def _api(query: str, variables: dict | None = None) -> dict:
    """One GraphQL call with retry/backoff on 429 and 5xx. Returns the
    `data` object; raises RuntimeError (readable message) otherwise."""
    key = os.environ.get("BUFFER_ACCESS_TOKEN", "")
    if not key:
        raise RuntimeError("BUFFER_ACCESS_TOKEN is not set")
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    last = "unknown error"
    for attempt in range(1, _RETRIES + 1):
        req = urllib.request.Request(
            API_URL, data=payload, method="POST",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json",
                     "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            detail = ""
            retry_after = 30 * attempt
            try:
                detail = e.read().decode()[:400]
                retry_after = int(e.headers.get("Retry-After") or retry_after)
            except Exception:
                pass
            last = f"HTTP {e.code} from Buffer: {detail or e.reason}"
            if e.code == 429 or e.code >= 500:
                print(f"  buffer: {last} - retry {attempt}/{_RETRIES} "
                      f"in {min(retry_after, 120)}s", file=sys.stderr)
                time.sleep(min(retry_after, 120))
                continue
            raise RuntimeError(last) from None
        except urllib.error.URLError as e:
            last = f"network error calling Buffer: {e.reason}"
            print(f"  buffer: {last} - retry {attempt}/{_RETRIES}", file=sys.stderr)
            time.sleep(5 * attempt)
            continue
        # GraphQL transport-level errors (auth, rate limit, bad query)
        errors = body.get("errors") or []
        if errors:
            code = ((errors[0].get("extensions") or {}).get("code")) or ""
            msg = errors[0].get("message") or "GraphQL error"
            last = f"{code or 'ERROR'}: {msg}"
            if code == "RATE_LIMIT_EXCEEDED":
                print(f"  buffer: rate limited - retry {attempt}/{_RETRIES}",
                      file=sys.stderr)
                time.sleep(min(60 * attempt, 120))
                continue
            raise RuntimeError(f"Buffer API error - {last}")
        return body.get("data") or {}
    raise RuntimeError(f"Buffer API failed after {_RETRIES} attempts: {last}")


# ---------------------------------------------------------------- account

_org_cache = {}


def organization_id() -> str:
    """The (first) organization of this Buffer account - each brand has
    its own account, so one organization per key. Cached per key."""
    key = os.environ.get("BUFFER_ACCESS_TOKEN", "")
    if key in _org_cache:
        return _org_cache[key]
    data = _api("query { organizations { id name } }")
    orgs = data.get("organizations") or []
    if not orgs:
        raise RuntimeError("Buffer account has no organization")
    _org_cache[key] = orgs[0]["id"]
    return _org_cache[key]


def list_channels() -> list[dict]:
    """Connected channels of this brand's Buffer account:
    [{id, service, name, isQueuePaused}] - service is the platform
    (instagram / facebook / tiktok / ...). Used for auto-wiring
    roster bufferChannels and by the doctor script."""
    org = organization_id()
    data = _api(
        """query($input: ChannelsInput!) {
             channels(input: $input) { id name displayName service isQueuePaused }
           }""",
        {"input": {"organizationId": org}})
    return data.get("channels") or []


def pending_count(channel_id: str) -> int:
    """How many posts are waiting in this channel's queue (status
    scheduled). One API call; capped at QUEUE_LIMIT because we only care
    whether the queue is full."""
    org = organization_id()
    data = _api(
        """query($first: Int, $input: PostsInput!) {
             posts(first: $first, input: $input) { edges { node { id } } }
           }""",
        {"first": QUEUE_LIMIT,
         "input": {"organizationId": org,
                   "filter": {"channelIds": [channel_id],
                              "status": ["scheduled"]}}})
    return len(((data.get("posts") or {}).get("edges")) or [])


# ------------------------------------------------------------- post build

def _assets(media: dict) -> list[dict]:
    if media.get("type") == "video":
        video = {"url": media["url"],
                 # pick the thumbnail ~1s in; custom thumbnail images are
                 # rejected by the API (media["cover"] cannot be used here)
                 "metadata": {"thumbnailOffset": 1000}}
        return [{"video": video}]
    image = {"url": media["url"]}
    return [{"image": image}]


def _metadata(service: str, media: dict) -> dict | None:
    """Service-specific metadata for the one network the channel belongs
    to. Only services we actively publish to get explicit settings; any
    other service posts with Buffer's defaults."""
    is_video = media.get("type") == "video"
    is_ai = bool(media.get("isAi"))
    if service == "instagram":
        ig = {"shouldShareToFeed": True,
              "type": "story" if media.get("story")
              else ("reel" if is_video else "post")}
        if is_ai:
            ig["isAiGenerated"] = True
        return {"instagram": ig}
    if service == "facebook":
        return {"facebook": {"type": "story" if media.get("story")
                             else ("reel" if is_video else "post")}}
    if service == "tiktok":
        tk = {}
        if is_ai and is_video:
            tk["isAiGenerated"] = True
        return {"tiktok": tk} if tk else None
    return None


_CREATE = """
mutation($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on PostActionSuccess { post { id status dueAt externalLink } }
    ... on MutationError { message }
  }
}
"""

_GET = """
query($input: PostInput!) {
  post(input: $input) { id status dueAt externalLink error { message } }
}
"""

_DELETE = """
mutation($input: DeletePostInput!) {
  deletePost(input: $input) {
    __typename
    ... on DeletePostSuccess { id }
    ... on MutationError { message }
  }
}
"""


def _create_one(channel: dict, media: dict, caption: str,
                scheduled_at: str | None) -> dict:
    """One createPost on one channel. Returns the created post node.
    Raises QueueFullError when the channel's queue is at its cap."""
    inp = {"text": caption,
           "channelId": channel["channelId"],
           "schedulingType": "automatic",
           "assets": _assets(media)}
    if scheduled_at:
        inp["mode"] = "customScheduled"
        inp["dueAt"] = scheduled_at
    else:
        inp["mode"] = "shareNow"
    meta = _metadata(channel.get("platform") or "", media)
    if meta:
        inp["metadata"] = meta
    res = (_api(_CREATE, {"input": inp}) or {}).get("createPost") or {}
    typename = res.get("__typename", "")
    if typename == "PostActionSuccess":
        return res["post"]
    msg = res.get("message") or f"createPost returned {typename or 'nothing'}"
    if typename == "LimitReachedError" or "limit" in msg.lower():
        raise QueueFullError(
            f"{channel.get('platform', '?')} queue is full "
            f"({QUEUE_LIMIT} pending posts): {msg}")
    raise RuntimeError(f"createPost failed on "
                       f"{channel.get('platform', '?')}: {msg}")


def _normalize_targets(targets) -> list[dict]:
    """targets: list of {platform, channelId} (a bare string is accepted
    as a single channel id with unknown platform, for symmetry with the
    zernio driver's legacy form)."""
    if isinstance(targets, str):
        return [{"platform": "", "channelId": targets}]
    out = []
    for t in targets or []:
        cid = t.get("channelId") or t.get("accountId")
        if cid:
            out.append({"platform": t.get("platform") or "", "channelId": cid})
    return out


_STATUS_MAP = {"sent": "published", "error": "failed",
               "scheduled": "scheduled", "sending": "publishing",
               "draft": "draft", "needs_approval": "draft"}


def _aggregate(posts: list[dict]) -> dict:
    """Fold per-channel Buffer posts into the shared driver result:
    {externalId, status, postUrl, urls, errors}. published only when ALL
    are sent; failed as soon as ANY errored."""
    urls, errors = {}, []
    statuses = []
    for p in posts:
        statuses.append(p.get("status") or "")
        if p.get("externalLink"):
            urls[p.get("_platform") or p["id"]] = p["externalLink"]
        err = (p.get("error") or {}).get("message")
        if err:
            errors.append({"platform": p.get("_platform") or "",
                           "message": str(err)[:400]})
    if statuses and all(s == "sent" for s in statuses):
        status = "published"
    elif any(s == "error" for s in statuses):
        status = "failed"
    elif any(s == "sending" for s in statuses):
        status = "publishing"
    else:
        status = _STATUS_MAP.get(statuses[0], statuses[0]) if statuses else ""
    return {"externalId": ",".join(p["id"] for p in posts),
            "status": status,
            "postUrl": next(iter(urls.values()), None),
            "urls": urls, "errors": errors}


# ------------------------------------------------------------ the contract

def check_queues(targets) -> list[str]:
    """Platforms whose queue is already full - call before scheduling to
    hold the item instead of burning a failed create. One API call per
    channel; only used when there is something to send."""
    full = []
    for t in _normalize_targets(targets):
        if pending_count(t["channelId"]) >= QUEUE_LIMIT:
            full.append(t.get("platform") or t["channelId"])
    return full


def schedule_post(targets, media: dict, caption: str, scheduled_at: str) -> dict:
    """Create one scheduled post per target channel (single post._id per
    channel; ids joined with ','). QueueFullError from any channel before
    anything was created aborts cleanly; once the first create succeeded,
    later queue-full channels are reported in errors instead, so we never
    lose the ids of posts that WERE created."""
    created = []
    for t in _normalize_targets(targets):
        try:
            post = _create_one(t, media, caption, scheduled_at)
        except QueueFullError:
            if not created:
                raise
            created.append({"id": "", "_platform": t["platform"],
                            "status": "error",
                            "error": {"message": "queue full - not created"}})
            continue
        post["_platform"] = t["platform"]
        post.setdefault("status", "scheduled")
        created.append(post)
        print(f"  buffer: scheduled {t.get('platform') or t['channelId']} "
              f"-> post {post['id']}")
    real = [p for p in created if p.get("id")]
    res = _aggregate(real)
    res["errors"] += [{"platform": p["_platform"],
                       "message": p["error"]["message"]}
                      for p in created if not p.get("id")]
    if res["status"] in ("", "draft"):
        res["status"] = "scheduled"
    return res


def publish_now(targets, media: dict, caption: str) -> dict:
    created = []
    for t in _normalize_targets(targets):
        post = _create_one(t, media, caption, None)
        post["_platform"] = t["platform"]
        post.setdefault("status", "sending")
        created.append(post)
        print(f"  buffer: shareNow {t.get('platform') or t['channelId']} "
              f"-> post {post['id']}")
    res = _aggregate(created)
    if res["status"] in ("", "draft", "scheduled"):
        res["status"] = "publishing"
    return res


def get_post(external_id: str) -> dict:
    """external_id may be a comma-joined list (one Buffer post per
    channel); statuses are aggregated - published only when every post
    is sent."""
    posts = []
    for pid in [x for x in str(external_id).split(",") if x]:
        data = _api(_GET, {"input": {"id": pid}})
        node = data.get("post") or {}
        node.setdefault("id", pid)
        posts.append(node)
    return _aggregate(posts)


def cancel_post(external_id: str) -> None:
    """Delete every not-yet-sent Buffer post behind this external id.
    A post that already went out cannot be deleted - reported, not
    raised, so cancels stay idempotent."""
    for pid in [x for x in str(external_id).split(",") if x]:
        res = (_api(_DELETE, {"input": {"id": pid}}) or {}).get("deletePost") or {}
        if res.get("__typename") != "DeletePostSuccess":
            print(f"  buffer: delete {pid}: "
                  f"{res.get('message') or res.get('__typename')}",
                  file=sys.stderr)
