"""Marketing caption generation via the Claude API (vision over video frames).

Falls back to a deterministic mock caption when no API key is configured so
the whole pipeline works offline.
"""
import base64
import time
from pathlib import Path
from typing import Mapping

from .. import config


class CaptionError(Exception):
    pass


SYSTEM_PROMPT = (
    "You are a social-media marketing copywriter specializing in short-form "
    "video captions that drive mobile app downloads. You write in English. "
    "You will be shown frames from a video that promotes a mobile app, plus "
    "details about the app. Write ONE caption for posting alongside the video. "
    "Requirements: "
    "(1) open with a scroll-stopping hook grounded in what is actually visible "
    "in the frames - reference the real content, not generic hype; "
    "(2) 1-3 short punchy sentences plus the CTA; "
    "(3) include a clear call-to-action to download/try the app; "
    "(4) include the app link exactly as given; "
    "(5) end with the provided hashtags (you may add up to 2 highly relevant ones); "
    "(6) use at most 2-3 fitting emojis; "
    "(7) apply short-form best practices - curiosity gap, urgency or social "
    "proof where natural, line breaks for scannability. "
    "Return ONLY the caption text - no preamble, no quotes, no explanations."
)

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic()
    return _client


def _mock_caption(asset: Mapping) -> str:
    parts = [
        f"🔥 You have to see what {asset['name']} can do!",
        "Tap the link and try it today 👇",
    ]
    if asset["link"]:
        parts.append(asset["link"])
    if asset["hashtags"]:
        parts.append(asset["hashtags"])
    parts.append("[MOCK]")
    return "\n".join(parts)


def generate_caption(frame_paths: list[Path], asset: Mapping) -> str:
    if config.CAPTION_MOCK or not config.ANTHROPIC_API_KEY:
        return _mock_caption(asset)

    import anthropic

    content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.standard_b64encode(f.read_bytes()).decode(),
            },
        }
        for f in frame_paths
    ]
    content.append({
        "type": "text",
        "text": (
            "These are frames from the video, in chronological order.\n\n"
            f"App name: {asset['name']}\n"
            f"App description / tone: {asset['description']}\n"
            f"Link (include verbatim): {asset['link']}\n"
            f"Hashtags (include at the end): {asset['hashtags']}\n\n"
            "Write the caption now."
        ),
    })

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            resp = _get_client().messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
            )
            break
        except anthropic.RateLimitError as e:
            last_error = e
            if attempt == 0:
                time.sleep(15)
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            raise CaptionError(f"Claude API error: {e}") from e
    else:
        raise CaptionError(f"Claude API rate limited: {last_error}")

    if resp.stop_reason == "refusal":
        raise CaptionError("Claude declined to generate a caption for this content")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not text:
        raise CaptionError("Claude returned an empty caption")
    return text
