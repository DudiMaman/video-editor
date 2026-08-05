"""Per-request processing: acquire source -> trim -> normalize outro ->
concat -> caption. Each step is idempotent (overwrites its own outputs), so a
request can safely be re-run after a crash or restart.
"""
import logging
import shutil
from pathlib import Path

from .. import config, db
from . import captions, downloader
from . import ffmpeg as ff

log = logging.getLogger(__name__)


def _resolve_data_path(rel: str) -> Path:
    return config.DATA_DIR / rel


def process_request(request_id: int) -> None:
    row = db.get_request(request_id)
    if row is None or row["status"] in ("done", "failed"):
        return
    try:
        source = _acquire_source(row)
        row = db.get_request(request_id)
        final_path, trimmed_path, clip_duration = _build_video(row, source)
        db.update_request(
            request_id,
            output_path=str(final_path.relative_to(config.DATA_DIR)),
        )
        try:
            _caption(row, trimmed_path, clip_duration)
        except captions.CaptionError as e:
            db.update_request(
                request_id, status="done", error=f"Caption failed: {e}",
                finished_at=_now(),
            )
            return
        finally:
            _cleanup(final_path.parent)
        db.update_request(request_id, status="done", error=None, finished_at=_now())
    except Exception as e:
        log.exception("request %s failed", request_id)
        db.update_request(
            request_id, status="failed", error=str(e)[:2000], finished_at=_now(),
        )


def recaption(request_id: int) -> None:
    """Re-run only the caption step for a finished request."""
    row = db.get_request(request_id)
    if row is None or not row["output_path"]:
        return
    try:
        final_path = _resolve_data_path(row["output_path"])
        frames_dir = config.FRAMES_DIR / str(request_id)
        frames = sorted(frames_dir.glob("frame_*.jpg"))
        if not frames:
            info = ff.probe(final_path)
            cut = row["cut_seconds"]
            duration = info["duration"] or cut or 10
            if cut:
                duration = min(cut, duration)
            frames = ff.extract_frames(final_path, frames_dir, duration)
        asset = db.get_db().execute(
            "SELECT * FROM assets WHERE id = ?", (row["asset_id"],)
        ).fetchone()
        caption = captions.generate_caption(frames, asset)
        db.update_request(request_id, status="done", caption=caption, error=None)
    except Exception as e:
        log.exception("recaption %s failed", request_id)
        db.update_request(request_id, status="done", error=f"Caption failed: {e}")


def _now() -> str:
    import datetime

    return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _acquire_source(row) -> Path:
    request_id = row["id"]
    if row["source_path"]:
        existing = _resolve_data_path(row["source_path"])
        if existing.exists():
            return existing
    if row["source_type"] != "url":
        raise ff.PipelineError("uploaded source file is missing from disk")
    db.set_status(request_id, "downloading")
    dest = config.UPLOADS_DIR / str(request_id)
    try:
        path = downloader.download(row["source_url"], dest)
    except downloader.DownloadError as e:
        raise ff.PipelineError(str(e)) from e
    db.update_request(request_id, source_path=str(path.relative_to(config.DATA_DIR)))
    return path


def _build_video(row, source: Path) -> tuple[Path, Path, float]:
    request_id = row["id"]
    db.set_status(request_id, "processing")

    outro_row = db.get_db().execute(
        "SELECT * FROM outros WHERE id = ?", (row["outro_id"],)
    ).fetchone()
    if outro_row is None:
        raise ff.PipelineError("outro no longer exists")
    outro_src = _resolve_data_path(outro_row["path"])
    if not outro_src.exists():
        raise ff.PipelineError("outro file is missing from disk")

    work_dir = config.OUTPUTS_DIR / str(request_id)
    work_dir.mkdir(parents=True, exist_ok=True)

    spec = ff.probe(source)
    cut = row["cut_seconds"]
    if cut is None or (spec["duration"] and cut >= spec["duration"]):
        cut = spec["duration"] or None

    trimmed = work_dir / "trimmed.mp4"
    ff.encode_canonical(source, trimmed, spec, spec["has_audio"], cut_seconds=cut)
    if cut is None:
        cut = ff.probe(trimmed)["duration"] or 10

    outro_spec = ff.probe(outro_src)
    outro_norm = work_dir / "outro_norm.mp4"
    ff.encode_canonical(outro_src, outro_norm, spec, outro_spec["has_audio"])

    final = work_dir / "final.mp4"
    ff.concat([trimmed, outro_norm], final)
    return final, trimmed, cut


def _caption(row, trimmed_path: Path, clip_duration: float) -> None:
    request_id = row["id"]
    db.set_status(request_id, "captioning")
    try:
        frames = ff.extract_frames(
            trimmed_path, config.FRAMES_DIR / str(request_id), clip_duration
        )
    except ff.PipelineError as e:
        raise captions.CaptionError(str(e)) from e
    asset = db.get_db().execute(
        "SELECT * FROM assets WHERE id = ?", (row["asset_id"],)
    ).fetchone()
    caption = captions.generate_caption(frames, asset)
    db.update_request(request_id, caption=caption)


def _cleanup(work_dir: Path) -> None:
    for name in ("trimmed.mp4", "outro_norm.mp4", "list.txt"):
        try:
            (work_dir / name).unlink(missing_ok=True)
        except OSError:
            pass
