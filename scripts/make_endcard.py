#!/usr/bin/env python3
"""Render a venture's end card (the closing frames appended to every
approved video) to the same technical spec the pipeline already ships.

Planty's card was produced externally; this script exists so later
ventures' cards are reproducible - re-run it to tweak wording or colour
instead of hand-editing an MP4 nobody can regenerate.

The spec is copied from outros/sample/*.mp4 so a card cuts together with
the rest of the library: 1080x1920, 30 fps, 3.70 s, H.264 High/yuv420p,
AAC stereo 44.1 kHz, and a 0.45 s fade in from black with no fade out.

The layout mirrors Planty's rhythm - badge, brand, rule, setup line, CTA,
where-to-go line, arrow - while the palette and copy come from each
venture's own brand.

Usage: python scripts/make_endcard.py acf
"""
import math
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
FONTS = Path("/mnt/skills/examples/canvas-design/canvas-fonts")
W, H, FPS, DURATION, FADE = 1080, 1920, 30, 3.70, 0.45

# Per-venture design. Colours are the venture's real brand values -
# ACF's are taken from ac-fund.org (charcoal #26231d, gold #e8a93c,
# cream #fbf8f2), the way Planty's card uses its own greens.
SPECS = {
    "acf": {
        "out": "outros/acf/acf-endcard-1080x1920.mp4",
        "bg": (0x22, 0x1F, 0x1A),
        "bg_center": (0x33, 0x2E, 0x25),
        "badge": (0x4C, 0x43, 0x31),
        "text": (0xFB, 0xF8, 0xF2),
        "muted": (0xD3, 0xCB, 0xBA),
        "rule": (0x6E, 0x66, 0x58),
        "accent": (0xE8, 0xA9, 0x3C),
        "icon": "heart",
        "brand": "African Children Fund",
        "setup": "$1 a day changes a life",
        "cta": "Donate Now",
        "where": "ac-fund.org",
    },
}


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def fit(text: str, name: str, size: int, max_w: int) -> ImageFont.FreeTypeFont:
    """Largest size at or below `size` that keeps `text` inside max_w."""
    while size > 10:
        f = font(name, size)
        if f.getbbox(text)[2] - f.getbbox(text)[0] <= max_w:
            return f
        size -= 2
    return font(name, size)


def centered(d: ImageDraw.ImageDraw, y: int, text: str,
             f: ImageFont.FreeTypeFont, fill: tuple) -> None:
    x0, _, x1, _ = d.textbbox((0, 0), text, font=f)
    d.text(((W - (x1 - x0)) / 2 - x0, y), text, font=f, fill=fill)


def background(spec: dict) -> Image.Image:
    """Flat brand colour lifted slightly toward the middle - the same
    soft radial the Planty card uses, so neither reads as flat fill."""
    im = Image.new("RGB", (W, H), spec["bg"])
    px = im.load()
    cx, cy = W / 2, H * 0.36
    far = math.hypot(max(cx, W - cx), max(cy, H - cy))
    for y in range(H):
        for x in range(0, W, 2):
            t = max(0.0, 1.0 - math.hypot(x - cx, y - cy) / far) ** 2
            c = tuple(int(a + (b - a) * t)
                      for a, b in zip(spec["bg"], spec["bg_center"]))
            px[x, y] = c
            if x + 1 < W:
                px[x + 1, y] = c
    return im


def heart(d: ImageDraw.ImageDraw, cx: float, cy: float, size: float,
          fill: tuple, width: int) -> None:
    pts = []
    for i in range(241):
        t = i * math.pi / 120
        pts.append((cx + size * (16 * math.sin(t) ** 3) / 16,
                    cy - size * (13 * math.cos(t) - 5 * math.cos(2 * t)
                                 - 2 * math.cos(3 * t) - math.cos(4 * t)) / 16))
    d.line(pts + [pts[0]], fill=fill, width=width, joint="curve")


def card(spec: dict) -> Image.Image:
    im = background(spec)
    d = ImageDraw.Draw(im)
    margin = 90
    maxw = W - 2 * margin

    # Badge + icon
    cx, cy, r = W / 2, H * 0.295, 178
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=spec["badge"])
    heart(d, cx, cy + 14, r * 0.68, spec["text"], 14)

    centered(d, int(H * 0.437), spec["brand"],
             fit(spec["brand"], "Outfit-Bold.ttf", 104, maxw), spec["text"])

    y = int(H * 0.545)
    d.line([(W / 2 - 62, y), (W / 2 + 62, y)], fill=spec["rule"], width=7)

    centered(d, int(H * 0.588), spec["setup"],
             fit(spec["setup"], "Outfit-Regular.ttf", 62, maxw), spec["muted"])
    centered(d, int(H * 0.655), spec["cta"],
             fit(spec["cta"], "Outfit-Bold.ttf", 116, maxw), spec["accent"])
    centered(d, int(H * 0.771), spec["where"],
             fit(spec["where"], "Outfit-Regular.ttf", 58, maxw), spec["text"])

    # Arrow: same weight and placement as the Planty card's.
    ax, top, bot, half = W / 2, H * 0.850, H * 0.922, 70
    d.line([(ax, top), (ax, bot)], fill=spec["text"], width=13)
    d.line([(ax - half, bot - half), (ax, bot)], fill=spec["text"], width=13)
    d.line([(ax + half, bot - half), (ax, bot)], fill=spec["text"], width=13)
    return im


def main() -> int:
    key = sys.argv[1] if len(sys.argv) > 1 else "acf"
    spec = SPECS.get(key)
    if spec is None:
        print(f"unknown venture {key!r}; known: {', '.join(SPECS)}",
              file=sys.stderr)
        return 2
    out = REPO / spec["out"]
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        still = Path(tmp) / "card.png"
        card(spec).save(still)
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-loop", "1", "-t", f"{DURATION}", "-i", str(still),
            "-f", "lavfi", "-t", f"{DURATION}", "-i", "anullsrc=r=44100:cl=stereo",
            "-vf", f"fade=t=in:st=0:d={FADE},format=yuv420p,fps={FPS}",
            "-c:v", "libx264", "-profile:v", "high", "-preset", "slow",
            "-crf", "20", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-ac", "2", "-shortest", "-movflags", "+faststart", str(out),
        ], check=True)
    print(f"wrote {spec['out']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
