import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

DATA_DIR = Path(os.environ.get("DATA_DIR") or ROOT_DIR / "data").resolve()
UPLOADS_DIR = DATA_DIR / "uploads"
OUTROS_DIR = DATA_DIR / "outros"
OUTPUTS_DIR = DATA_DIR / "outputs"
FRAMES_DIR = DATA_DIR / "frames"
DB_PATH = DATA_DIR / "app.db"

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL") or "claude-sonnet-5"
FFMPEG_WORKERS = int(os.environ.get("FFMPEG_WORKERS") or "2")
CAPTION_MOCK = os.environ.get("CAPTION_MOCK") == "1"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY") or ""

FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"


def ensure_dirs() -> None:
    for d in (DATA_DIR, UPLOADS_DIR, OUTROS_DIR, OUTPUTS_DIR, FRAMES_DIR):
        d.mkdir(parents=True, exist_ok=True)
