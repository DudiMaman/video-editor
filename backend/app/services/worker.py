"""Background job processing: a persistent job table (requests) + in-memory
queue consumed by worker threads. The DB is the source of truth; the queue is
just a wake-up signal, so restarts never lose work.
"""
import logging
import queue
import threading

from .. import config, db
from . import pipeline

log = logging.getLogger(__name__)

_q: "queue.Queue[tuple[str, int] | None]" = queue.Queue()
_threads: list[threading.Thread] = []


def enqueue(kind: str, request_id: int) -> None:
    _q.put((kind, request_id))


def _loop() -> None:
    while True:
        item = _q.get()
        if item is None:
            return
        kind, request_id = item
        try:
            if kind == "process":
                pipeline.process_request(request_id)
            elif kind == "recaption":
                pipeline.recaption(request_id)
        except Exception:
            log.exception("worker crashed on %s %s", kind, request_id)


def start() -> None:
    conn = db.get_db()
    conn.execute(
        "UPDATE requests SET status = 'queued', updated_at = datetime('now') "
        "WHERE status IN ('downloading', 'processing', 'captioning')"
    )
    conn.commit()
    for row in conn.execute("SELECT id FROM requests WHERE status = 'queued' ORDER BY id"):
        enqueue("process", row["id"])
    for i in range(config.FFMPEG_WORKERS):
        t = threading.Thread(target=_loop, daemon=True, name=f"video-worker-{i}")
        t.start()
        _threads.append(t)


def stop() -> None:
    for _ in _threads:
        _q.put(None)
