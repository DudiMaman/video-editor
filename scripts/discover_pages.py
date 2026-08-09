#!/usr/bin/env python3
"""Discovery agent: learn the inbox seed pages and find similar ones online.

For every app that has active seed pages in data/inbox.json, asks Claude
(with the web_search server tool) to study the seeds and the brief, then
search the web for creator pages with similar content. New finds are
appended to data/suggestions.json as status="suggested" for the user to
accept or dismiss in the UI. Pages already tracked, or already suggested
(any status, including dismissed), are never proposed again.
"""
import json
import os
import re
import sys
import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

MODEL = os.environ.get("CLAUDE_MODEL") or "claude-sonnet-5"
MAX_PER_APP = int(os.environ.get("DISCOVER_MAX_PER_APP") or 8)
MAX_SEARCHES = int(os.environ.get("DISCOVER_MAX_SEARCHES") or 10)
# The daily schedule keeps producing finds; don't pile up more than this
# many unhandled suggestions per app.
MAX_PENDING_PER_APP = int(os.environ.get("DISCOVER_MAX_PENDING") or 10)

PROMPT = """\
You are a short-form-video content scout for the mobile app "{app_name}".
App description: {app_desc}

The user already follows these pages as sources (the "seeds"):
{seeds}

Editorial brief describing what content qualifies:
---
{brief}
---

Task: find up to {max_new} NEW official TIKTOK ACCOUNT PAGES of mobile
apps that operate in the same specific domain as the seeds. Study what
the seed pages are about, then search the web for the official TikTok
pages of other apps in that exact niche - that is where the most
relevant videos are.

STRICT page-type rules - only suggest a page when it is clearly the
OFFICIAL account of an app or app-company:
- Signals: the handle or bio names an app, the bio links to an app-store
  page or the app's website, the content demos the app itself.
- Do NOT suggest hobbyists, enthusiasts, or niche influencers - however
  on-topic their content is.
- Do NOT suggest pages of private individuals or personal creator brands.
- Do NOT suggest magazines, communities, aggregators, or hashtag/discover
  pages.

Other rules:
- TIKTOK ONLY: suggest exclusively tiktok.com/@... account pages. Never
  suggest Instagram, YouTube, or any other platform - the downstream
  pipeline can only scan TikTok. If an app fits perfectly but has no
  TikTok page, do not suggest it at all.
- Prefer pages whose recent posts are in ENGLISH - the editorial brief
  rejects foreign-language content, so a non-English page is useless.
- Suggest account PAGES, not individual videos.
- Never suggest any of these already-known URLs:
{known}
- Prefer active pages that post regularly.
- Respond with ONLY a JSON array, no other text:
  [{{"url": "https://...", "name": "app / page name", "reason": "one short sentence in Hebrew naming the app and why it fits"}}]
- If you cannot find good candidates, return [].
"""


def norm_url(u: str) -> str:
    """Canonical form so the same page never slips through twice
    (https/http, www./m., trailing slash, query strings)."""
    u = (u or "").strip().lower()
    u = re.sub(r"[?#].*$", "", u).rstrip("/")
    u = re.sub(r"^https?://", "", u)
    return re.sub(r"^(www\.|m\.)", "", u)


def load_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return fallback


def extract_json_array(text: str):
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def discover_for_app(client, app, seeds, brief, known_urls):
    prompt = PROMPT.format(
        app_name=app.get("name", app["id"]),
        app_desc=app.get("description") or "(no description)",
        seeds="\n".join(f"- {p['url']}" for p in seeds),
        brief=brief[:6000],
        max_new=MAX_PER_APP,
        known="\n".join(f"- {u}" for u in sorted(known_urls)) or "- (none)",
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": MAX_SEARCHES,
        }],
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    found = []
    for item in extract_json_array(text):
        url = str(item.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        if norm_url(url) in known_urls:
            continue
        found.append({
            "url": url,
            "name": str(item.get("name") or "").strip()[:120],
            "reason": str(item.get("reason") or "").strip()[:300],
        })
    return found[:MAX_PER_APP]


def main() -> int:
    inbox = load_json(REPO_ROOT / "data" / "inbox.json", [])
    assets = load_json(REPO_ROOT / "assets.json", [])
    brief = (REPO_ROOT / "data" / "brief.md").read_text(encoding="utf-8") \
        if (REPO_ROOT / "data" / "brief.md").exists() else ""
    sug_path = REPO_ROOT / "data" / "suggestions.json"
    suggestions = load_json(sug_path, [])

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set - cannot run discovery", file=sys.stderr)
        return 1

    import anthropic
    client = anthropic.Anthropic()

    known = {norm_url(p["url"]) for p in inbox} | {norm_url(s["url"]) for s in suggestions}
    today = datetime.date.today().isoformat()
    added = 0

    for app in assets:
        seeds = [p for p in inbox
                 if str(p.get("asset")) == str(app["id"]) and p.get("active")]
        if not seeds:
            continue
        pending = sum(1 for s in suggestions
                      if str(s.get("asset")) == str(app["id"])
                      and s.get("status") == "suggested")
        if pending >= MAX_PENDING_PER_APP:
            print(f"skipping {app['id']}: {pending} suggestions already await review")
            continue
        print(f"discovering for {app['id']} ({len(seeds)} seed pages)...")
        try:
            found = discover_for_app(client, app, seeds, brief, known)
        except Exception as e:  # one app failing must not stop the others
            print(f"  FAILED: {e}", file=sys.stderr)
            continue
        for f in found:
            known.add(norm_url(f["url"]))
            suggestions.append({
                "url": f["url"],
                "name": f["name"],
                "reason": f["reason"],
                "asset": app["id"],
                "found_at": today,
                "status": "suggested",
            })
            added += 1
        print(f"  {len(found)} new suggestion(s)")

    if added:
        sug_path.write_text(
            json.dumps(suggestions, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
    print(f"total new suggestions: {added}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
