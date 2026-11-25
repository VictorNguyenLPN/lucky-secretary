"""SQLite connection manager and schema initialization using aiosqlite."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id TEXT NOT NULL UNIQUE,
    timezone TEXT NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_requests (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    event_payload TEXT NOT NULL,
    telegram_message_id TEXT,
    google_event_id TEXT,
    event_url TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calendar_requests_user_status
    ON calendar_requests(user_id, status);

CREATE INDEX IF NOT EXISTS idx_calendar_requests_expires_at
    ON calendar_requests(expires_at);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    state TEXT NOT NULL DEFAULT 'IDLE',
    pending_request_id TEXT REFERENCES calendar_requests(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL
);
"""


class DatabaseManager:
    """Manages SQLite database initialization and connections."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._memory_conn: aiosqlite.Connection | None = None

    async def init_db(self) -> None:
        """Create database parent directory (if file-backed) and initialize tables."""
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with self.connect() as conn:
            await conn.executescript(SCHEMA_SQL)
            await conn.commit()

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        """Yield an aiosqlite connection with foreign keys and Row factory enabled."""
        if self.db_path == ":memory:":
            if self._memory_conn is None:
                self._memory_conn = await aiosqlite.connect(":memory:")
                self._memory_conn.row_factory = aiosqlite.Row
                await self._memory_conn.execute("PRAGMA foreign_keys = ON;")
            yield self._memory_conn
            return

        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys = ON;")
            yield conn

    async def close(self) -> None:
        """Close persistent in-memory connection if open."""
        if self._memory_conn is not None:
            await self._memory_conn.close()
            self._memory_conn = None

