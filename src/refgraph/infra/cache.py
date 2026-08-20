from __future__ import annotations

import json
import sqlite3
from typing import Any


class ResponseCache:
    """Persistent cache for raw API responses, keyed by (source, url).

    Pass ``path=None`` to disable caching.
    """

    def __init__(self, path: str | None) -> None:
        self._conn: sqlite3.Connection | None = None
        if path:
            self._conn = sqlite3.connect(path)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS responses ("
                "  cache_key TEXT PRIMARY KEY,"
                "  payload TEXT NOT NULL,"
                "  created_at REAL NOT NULL DEFAULT (unixepoch())"
                ")"
            )
            self._conn.commit()

    def get(self, cache_key: str) -> Any | None:
        if self._conn is None:
            return None
        row = self._conn.execute(
            "SELECT payload FROM responses WHERE cache_key = ?", (cache_key,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, cache_key: str, payload: str) -> None:
        if self._conn is None:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO responses (cache_key, payload, created_at)"
            " VALUES (?, ?, unixepoch())",
            (cache_key, payload),
        )
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> ResponseCache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
