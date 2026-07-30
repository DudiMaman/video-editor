"""Source video acquisition from URLs via yt-dlp (handles both direct video
URLs and platform links like YouTube/TikTok)."""
from pathlib import Path

import yt_dlp


class DownloadError(Exception):
    pass


def download(url: str, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "outtmpl": str(dest_dir / "source.%(ext)s"),
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(f"download failed: {e}") from e

    downloads = (info or {}).get("requested_downloads") or []
    for d in downloads:
        filepath = d.get("filepath")
        if filepath and Path(filepath).exists():
            return Path(filepath)
    candidates = sorted(dest_dir.glob("source.*"))
    if not candidates:
        raise DownloadError("download finished but no file was produced")
    return candidates[0]
