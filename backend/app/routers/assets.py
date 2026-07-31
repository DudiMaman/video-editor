import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import config, db
from ..schemas import AssetIn

router = APIRouter(tags=["assets"])

ALLOWED_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}


def _asset_dict(row, outros: list[dict]) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "link": row["link"],
        "hashtags": row["hashtags"],
        "created_at": row["created_at"],
        "outros": outros,
    }


def _outros_for(conn, asset_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, original_name FROM outros WHERE asset_id = ? ORDER BY id",
        (asset_id,),
    ).fetchall()
    return [{"id": r["id"], "original_name": r["original_name"]} for r in rows]


@router.get("/assets")
def list_assets():
    conn = db.get_db()
    assets = conn.execute("SELECT * FROM assets ORDER BY id").fetchall()
    return [_asset_dict(a, _outros_for(conn, a["id"])) for a in assets]


@router.post("/assets", status_code=201)
def create_asset(payload: AssetIn):
    conn = db.get_db()
    cur = conn.execute(
        "INSERT INTO assets (name, description, link, hashtags) VALUES (?, ?, ?, ?)",
        (payload.name.strip(), payload.description.strip(),
         payload.link.strip(), payload.hashtags.strip()),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM assets WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _asset_dict(row, [])


@router.put("/assets/{asset_id}")
def update_asset(asset_id: int, payload: AssetIn):
    conn = db.get_db()
    if conn.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone() is None:
        raise HTTPException(404, "asset not found")
    conn.execute(
        "UPDATE assets SET name = ?, description = ?, link = ?, hashtags = ? WHERE id = ?",
        (payload.name.strip(), payload.description.strip(),
         payload.link.strip(), payload.hashtags.strip(), asset_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return _asset_dict(row, _outros_for(conn, asset_id))


@router.delete("/assets/{asset_id}", status_code=204)
def delete_asset(asset_id: int):
    conn = db.get_db()
    if conn.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone() is None:
        raise HTTPException(404, "asset not found")
    used = conn.execute(
        "SELECT 1 FROM requests WHERE asset_id = ? LIMIT 1", (asset_id,)
    ).fetchone()
    if used:
        raise HTTPException(409, "לא ניתן למחוק נכס שכבר שימש בבקשות עיבוד")
    conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    conn.commit()
    shutil.rmtree(config.OUTROS_DIR / str(asset_id), ignore_errors=True)


@router.post("/assets/{asset_id}/outros", status_code=201)
def upload_outro(asset_id: int, file: UploadFile):
    conn = db.get_db()
    if conn.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone() is None:
        raise HTTPException(404, "asset not found")
    original_name = file.filename or "outro.mp4"
    suffix = Path(original_name).suffix.lower() or ".mp4"
    if suffix not in ALLOWED_VIDEO_SUFFIXES:
        raise HTTPException(400, f"סוג קובץ לא נתמך: {suffix}")

    cur = conn.execute(
        "INSERT INTO outros (asset_id, original_name) VALUES (?, ?)",
        (asset_id, original_name),
    )
    outro_id = cur.lastrowid
    dest_dir = config.OUTROS_DIR / str(asset_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{outro_id}{suffix}"
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    rel = str(dest.relative_to(config.DATA_DIR))
    conn.execute("UPDATE outros SET path = ? WHERE id = ?", (rel, outro_id))
    conn.commit()
    return {"id": outro_id, "original_name": original_name}


@router.delete("/outros/{outro_id}", status_code=204)
def delete_outro(outro_id: int):
    conn = db.get_db()
    row = conn.execute("SELECT * FROM outros WHERE id = ?", (outro_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "outro not found")
    used = conn.execute(
        "SELECT 1 FROM requests WHERE outro_id = ? LIMIT 1", (outro_id,)
    ).fetchone()
    if used:
        raise HTTPException(409, "לא ניתן למחוק סגיר שכבר שימש בבקשות עיבוד")
    conn.execute("DELETE FROM outros WHERE id = ?", (outro_id,))
    conn.commit()
    if row["path"]:
        (config.DATA_DIR / row["path"]).unlink(missing_ok=True)


@router.get("/outros/{outro_id}/file")
def stream_outro(outro_id: int):
    row = db.get_db().execute(
        "SELECT * FROM outros WHERE id = ?", (outro_id,)
    ).fetchone()
    if row is None or not row["path"]:
        raise HTTPException(404, "outro not found")
    path = config.DATA_DIR / row["path"]
    if not path.exists():
        raise HTTPException(404, "outro file missing from disk")
    return FileResponse(path, media_type="video/mp4")
