"""SQLite key-value store for user-editable operational configuration.

Secrets and infrastructure settings remain in .env — this store holds
everything else (risk limits, scan params, watchlist, conviction
thresholds, etc.) so the Settings UI can persist changes that take
effect immediately without a restart.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


class ConfigStore:
    """Persistent key-value store backed by SQLite.

    Values are stored as JSON strings so that lists, dicts, numbers, and
    booleans round-trip correctly.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._ensure_table()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self.db_path))

    def _ensure_table(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS config ("
                "  key TEXT PRIMARY KEY,"
                "  value TEXT NOT NULL,"
                "  updated_at TEXT NOT NULL"
                ")"
            )

    def get_all(self) -> dict[str, Any]:
        """Read all config entries, JSON-decoding each value."""
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM config").fetchall()
        result: dict[str, Any] = {}
        for key, raw in rows:
            try:
                result[key] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                result[key] = raw
        return result

    def get(self, key: str) -> Any | None:
        """Read a single config value (None if missing)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM config WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return row[0]

    def set_many(self, updates: dict[str, Any]) -> None:
        """Write multiple config entries atomically."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO config (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                [(k, json.dumps(v), now) for k, v in updates.items()],
            )
        logger.info("config_store_updated", keys=list(updates.keys()))

    def set(self, key: str, value: Any) -> None:
        """Write a single config entry."""
        self.set_many({key: value})

    def delete(self, key: str) -> None:
        """Remove a config entry (revert to default)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM config WHERE key = ?", (key,))

    @property
    def is_empty(self) -> bool:
        """True when no config entries have been persisted yet."""
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM config").fetchone()
        return row[0] == 0
