import json
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from .. import config, db
from ..schemas import BatchRequestIn
from ..services import worker

router = APIRouter(tags=["batches"])


def _request_dict(row) -> dict:
    return {
        "id": row["id"],
        "batch_id": row["batch_id"],
        "asset_id": row["asset_id"],
        "outro_id": row["outro_id"],
        "source_type": row["source_type"],
        "source_url": row["source_url"],
        "cut_seconds": row["cut_seconds"],
        "status": row["status"],
        "error": row["error"],
        "has_output": bool(row["output_path"]),
        "caption": row["caption"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
    }


@router.post("/batches", status_code=201)
async def create_batch(request: Request):
    form = await request.form()
    raw = form.get("requests")
    if not isinstance(raw, str):
        raise HTTPException(422, "missing 'requests' form field")
    try:
        items = [BatchRequestIn.model_validate(i) for i in json.loads(raw)]
    except (json.JSONDecodeError, ValidationError) as e:
        raise HTTPException(422, f"invalid requests payload: {e}")
    if not items:
        raise HTTPException(422, "batch is empty")

    conn = db.get_db()
    for idx, item in enumerate(items):
        asset = conn.execute(
            "SELECT 1 FROM assets WHERE id = ?", (item.asset_id,)
        ).fetchone()
        if asset is None:
            raise HTTPException(422, f"request {idx + 1}: asset does not exist")
        outro = conn.execute(
            "SELECT asset_id FROM outros WHERE id = ?", (item.outro_id,)
        ).fetchone()
        if outro is None or outro["asset_id"] != item.asset_id:
            raise HTTPException(422, f"request {idx + 1}: outro does not belong to asset")
        if item.source_type == "url":
            if not (item.source_url or "").strip():
                raise HTTPException(422, f"request {idx + 1}: missing source URL")
        else:
            if not item.file_key or not isinstance(form.get(item.file_key), UploadFile):
                raise HTTPException(422, f"request {idx + 1}: missing uploaded file")

    cur = conn.execute("INSERT INTO batches DEFAULT VALUES")
    batch_id = cur.lastrowid
    created = []
    for item in items:
        cur = conn.execute(
            "INSERT INTO requests (batch_id, asset_id, outro_id, source_type, "
            "source_url, cut_seconds) VALUES (?, ?, ?, ?, ?, ?)",
            (batch_id, item.asset_id, item.outro_id, item.source_type,
             (item.source_url or "").strip() or None, item.cut_seconds),
        )
        request_id = cur.lastrowid
        if item.source_type == "upload":
            upload: UploadFile = form.get(item.file_key)
            suffix = Path(upload.filename or "").suffix.lower() or ".mp4"
            dest_dir = config.UPLOADS_DIR / str(request_id)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"source{suffix}"
            with dest.open("wb") as out:
                shutil.copyfileobj(upload.file, out)
            conn.execute(
                "UPDATE requests SET source_path = ? WHERE id = ?",
                (str(dest.relative_to(config.DATA_DIR)), request_id),
            )
        created.append(request_id)
    conn.commit()

    for request_id in created:
        worker.enqueue("process", request_id)

    rows = conn.execute(
        "SELECT * FROM requests WHERE batch_id = ? ORDER BY id", (batch_id,)
    ).fetchall()
    return {"id": batch_id, "requests": [_request_dict(r) for r in rows]}


@router.get("/batches")
def list_batches(limit: int = 20):
    conn = db.get_db()
    batches = conn.execute(
        "SELECT * FROM batches ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    result = []
    for b in batches:
        rows = conn.execute(
            "SELECT * FROM requests WHERE batch_id = ? ORDER BY id", (b["id"],)
        ).fetchall()
        result.append({
            "id": b["id"],
            "created_at": b["created_at"],
            "requests": [_request_dict(r) for r in rows],
        })
    return result


@router.get("/batches/{batch_id}")
def get_batch(batch_id: int):
    conn = db.get_db()
    b = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if b is None:
        raise HTTPException(404, "batch not found")
    rows = conn.execute(
        "SELECT * FROM requests WHERE batch_id = ? ORDER BY id", (batch_id,)
    ).fetchall()
    return {"id": b["id"], "created_at": b["created_at"],
            "requests": [_request_dict(r) for r in rows]}


@router.get("/requests/{request_id}")
def get_request(request_id: int):
    row = db.get_request(request_id)
    if row is None:
        raise HTTPException(404, "request not found")
    return _request_dict(row)


@router.get("/requests/{request_id}/video")
def get_request_video(request_id: int, download: int = 0):
    row = db.get_request(request_id)
    if row is None:
        raise HTTPException(404, "request not found")
    if not row["output_path"]:
        raise HTTPException(404, "no output video yet")
    path = config.DATA_DIR / row["output_path"]
    if not path.exists():
        raise HTTPException(404, "output file missing from disk")
    kwargs = {}
    if download:
        kwargs["filename"] = f"video_{request_id}.mp4"
    return FileResponse(path, media_type="video/mp4", **kwargs)


@router.post("/requests/{request_id}/recaption", status_code=202)
def recaption_request(request_id: int):
    row = db.get_request(request_id)
    if row is None:
        raise HTTPException(404, "request not found")
    if row["status"] != "done" or not row["output_path"]:
        raise HTTPException(409, "ניתן לחדש כיתוב רק לבקשה שהסתיימה בהצלחה")
    db.set_status(request_id, "captioning")
    worker.enqueue("recaption", request_id)
    return {"status": "captioning"}
