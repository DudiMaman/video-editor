import base64
import logging
import secrets
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from . import config, db
from .routers import assets, batches
from .services import worker

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    worker.start()
    yield
    worker.stop()


app = FastAPI(title="Video Editor", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if config.APP_PASSWORD:
    # Optional password gate for public deployments (any username, HTTP Basic).
    # /api/health stays open for platform health checks.
    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        if request.url.path != "/api/health":
            header = request.headers.get("authorization", "")
            ok = False
            if header.startswith("Basic "):
                try:
                    decoded = base64.b64decode(header[6:]).decode()
                    _, _, password = decoded.partition(":")
                    ok = secrets.compare_digest(password, config.APP_PASSWORD)
                except (ValueError, UnicodeDecodeError):
                    ok = False
            if not ok:
                return Response(
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="video-editor"'},
                )
        return await call_next(request)


app.include_router(assets.router, prefix="/api")
app.include_router(batches.router, prefix="/api")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "captions": "mock" if (config.CAPTION_MOCK or not config.ANTHROPIC_API_KEY) else "claude",
    }


if config.FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=config.FRONTEND_DIST, html=True), name="static")
