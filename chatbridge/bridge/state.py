"""Persistent mapping from a Delta Chat chat to an OpenCode session."""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    accid      INTEGER NOT NULL,
    chat_id    INTEGER NOT NULL,
    session_id TEXT    NOT NULL,
    created_at REAL    NOT NULL,
    PRIMARY KEY (accid, chat_id)
);
"""


class State:
    """Tiny SQLite-backed store, opened with ``check_same_thread=False`` so the
    per-chat worker threads can use it directly."""

    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def get_session(self, accid: int, chat_id: int) -> Optional[str]:
        with self._lock:
            row = self._db.execute(
                "SELECT session_id FROM sessions WHERE accid = ? AND chat_id = ?",
                (accid, chat_id),
            ).fetchone()
        return row[0] if row else None

    def set_session(self, accid: int, chat_id: int, session_id: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO sessions "
                "(accid, chat_id, session_id, created_at) VALUES (?, ?, ?, ?)",
                (accid, chat_id, session_id, time.time()),
            )
            self._db.commit()

    def clear_session(self, accid: int, chat_id: int) -> None:
        with self._lock:
            self._db.execute(
                "DELETE FROM sessions WHERE accid = ? AND chat_id = ?", (accid, chat_id)
            )
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()
