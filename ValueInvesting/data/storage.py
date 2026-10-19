from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CacheStorage:
    """Small JSON cache backed by SQLite."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cache (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    source TEXT NOT NULL
                )
                """
            )

    def put(self, key: str, payload: Any, source: str) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        fetched_at = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO cache(cache_key, payload, fetched_at, source)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload = excluded.payload,
                    fetched_at = excluded.fetched_at,
                    source = excluded.source
                """,
                (key, encoded, fetched_at, source),
            )

    def get(self, key: str, max_age_seconds: float | None = None) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload, fetched_at, source FROM cache WHERE cache_key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None

        fetched_at = datetime.fromisoformat(row["fetched_at"])
        age_seconds = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        if max_age_seconds is not None and age_seconds > max_age_seconds:
            return None
        return {
            "payload": json.loads(row["payload"]),
            "fetched_at": row["fetched_at"],
            "source": row["source"],
            "age_seconds": age_seconds,
        }
