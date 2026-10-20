from __future__ import annotations

import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (request_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS flagged (
    request_id INTEGER PRIMARY KEY,
    media_id INTEGER NOT NULL,
    tmdb_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    user_name TEXT NOT NULL,
    title TEXT NOT NULL,
    trigger_request_id INTEGER NOT NULL,
    flagged_at REAL NOT NULL,
    state TEXT NOT NULL DEFAULT 'flagged'  -- flagged | kept | cleared | purged
);
CREATE TABLE IF NOT EXISTS untrimmable (request_id INTEGER PRIMARY KEY);
"""


def _locked(fn):
    def wrapper(self, *a, **kw):
        with self._lock:
            return fn(self, *a, **kw)
    wrapper.__name__ = fn.__name__
    return wrapper


class Store:
    def __init__(self, path: str):
        # Shared by the poller thread and the web UI threads; every method takes the lock.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    @_locked
    def is_empty(self) -> bool:
        return self._db.execute("SELECT 1 FROM seen LIMIT 1").fetchone() is None

    @_locked
    def seen(self) -> set[int]:
        return {r[0] for r in self._db.execute("SELECT request_id FROM seen")}

    @_locked
    def mark_seen(self, *ids: int) -> None:
        self._db.executemany("INSERT OR IGNORE INTO seen VALUES (?)", [(i,) for i in ids])
        self._db.commit()

    @_locked
    def warn_untrimmable_once(self, request_id: int) -> bool:
        cur = self._db.execute("INSERT OR IGNORE INTO untrimmable VALUES (?)", (request_id,))
        self._db.commit()
        return cur.rowcount == 1

    @_locked
    def flag(self, request_id, media_id, tmdb_id, user_id, user_name, title, trigger_request_id) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO flagged VALUES (?,?,?,?,?,?,?,?,'flagged')",
            (request_id, media_id, tmdb_id, user_id, user_name, title, trigger_request_id, time.time()),
        )
        self._db.commit()

    @_locked
    def is_flagged(self, request_id: int) -> bool:
        """True once a request has ever been flagged, so a cleared one is not flagged again."""
        return self._db.execute("SELECT 1 FROM flagged WHERE request_id=?", (request_id,)).fetchone() is not None

    @_locked
    def flagged(self, state: str = "flagged") -> list[sqlite3.Row]:
        return self._db.execute(
            "SELECT * FROM flagged WHERE state=? ORDER BY flagged_at", (state,)
        ).fetchall()

    @_locked
    def set_state(self, request_id: int, state: str) -> None:
        self._db.execute("UPDATE flagged SET state=? WHERE request_id=?", (state, request_id))
        self._db.commit()

    @_locked
    def get_flag(self, request_id: int):
        return self._db.execute("SELECT * FROM flagged WHERE request_id=?", (request_id,)).fetchone()

    @_locked
    def history(self, limit: int = 50) -> list[sqlite3.Row]:
        return self._db.execute(
            "SELECT * FROM flagged WHERE state != 'flagged' ORDER BY flagged_at DESC LIMIT ?", (limit,)
        ).fetchall()
