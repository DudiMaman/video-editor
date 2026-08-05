import sqlite3
import threading

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  link        TEXT NOT NULL DEFAULT '',
  hashtags    TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS outros (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  asset_id      INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  original_name TEXT NOT NULL,
  path          TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS batches (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS requests (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id     INTEGER NOT NULL REFERENCES batches(id),
  asset_id     INTEGER NOT NULL REFERENCES assets(id),
  outro_id     INTEGER NOT NULL REFERENCES outros(id),
  source_type  TEXT NOT NULL CHECK (source_type IN ('upload','url')),
  source_url   TEXT,
  source_path  TEXT,
  cut_seconds  REAL CHECK (cut_seconds IS NULL OR cut_seconds > 0),
  status       TEXT NOT NULL DEFAULT 'queued'
               CHECK (status IN ('queued','downloading','processing','captioning','done','failed')),
  error        TEXT,
  output_path  TEXT,
  caption      TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_requests_batch  ON requests(batch_id);
CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
"""

_local = threading.local()


def get_db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(config.DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    config.ensure_dirs()
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()


def get_request(request_id: int) -> sqlite3.Row | None:
    return get_db().execute(
        "SELECT * FROM requests WHERE id = ?", (request_id,)
    ).fetchone()


def update_request(request_id: int, **fields) -> None:
    assignments = ", ".join(f"{k} = ?" for k in fields)
    conn = get_db()
    conn.execute(
        f"UPDATE requests SET {assignments}, updated_at = datetime('now') WHERE id = ?",
        (*fields.values(), request_id),
    )
    conn.commit()


def set_status(request_id: int, status: str, error: str | None = None) -> None:
    update_request(request_id, status=status, error=error)
