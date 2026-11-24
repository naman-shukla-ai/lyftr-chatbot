"""SQLite storage for session history.

A short-lived connection is opened per operation (cheap for SQLite, and safe to
use from worker threads). The schema is created on startup.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  TEXT NOT NULL,
  role        TEXT NOT NULL,   -- 'user' or 'assistant'
  content     TEXT NOT NULL,
  metadata    TEXT,            -- JSON string, assistant messages only
  created_at  TEXT NOT NULL    -- server time, ISO-8601
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, id);
CREATE TABLE IF NOT EXISTS sessions (
  session_id    TEXT PRIMARY KEY,
  system_prompt TEXT,
  created_at    TEXT NOT NULL
);
"""


def sqlite_path(database_url: str) -> str:
    """Turn a SQLAlchemy-style URL (sqlite:////data/app.db) into a filesystem path."""
    url = database_url.strip()
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///"):]
    if url.startswith("sqlite://"):
        return url[len("sqlite://"):]
    return url  # already a plain path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, database_url: str) -> None:
        self.path = sqlite_path(database_url)
        if not self.path or self.path == ":memory:":
            raise ValueError("DATABASE_URL must point at a file (in-memory databases are not supported)")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    def ping(self) -> bool:
        try:
            with self._conn() as conn:
                conn.execute("SELECT 1 FROM messages LIMIT 1").fetchall()
            return True
        except Exception:
            return False

    def session_exists(self, session_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            return row is not None

    def get_system_prompt(self, session_id: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute("SELECT system_prompt FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            return row["system_prompt"] if row else None

    def get_history(self, session_id: str, limit: Optional[int] = None) -> list[dict[str, Any]]:
        """Messages oldest-first. With `limit`, only the most recent `limit` messages."""
        with self._conn() as conn:
            if limit:
                rows = conn.execute(
                    "SELECT role, content, metadata FROM messages WHERE session_id = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
                rows = list(reversed(rows))
            else:
                rows = conn.execute(
                    "SELECT role, content, metadata FROM messages WHERE session_id = ? ORDER BY id ASC",
                    (session_id,),
                ).fetchall()
        return [
            {
                "role": r["role"],
                "content": r["content"],
                "metadata": json.loads(r["metadata"]) if r["metadata"] else None,
            }
            for r in rows
        ]

    def save_exchange(
        self,
        session_id: str,
        system_prompt: Optional[str],
        user_content: str,
        assistant_content: str,
        metadata: dict[str, Any],
    ) -> None:
        """Persist the user message and the assistant reply atomically."""
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, system_prompt, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "system_prompt = COALESCE(excluded.system_prompt, sessions.system_prompt)",
                (session_id, system_prompt, now),
            )
            conn.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, 'user', ?, NULL, ?)",
                (session_id, user_content, now),
            )
            conn.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, 'assistant', ?, ?, ?)",
                (session_id, assistant_content, json.dumps(metadata), _now()),
            )
