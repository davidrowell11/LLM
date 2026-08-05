"""A small persistent queue of topics for the autonomous research daemon.

Separate from Memory's notes table, but lives in the same SQLite file so
everything the agent has learned or plans to learn is in one place.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from . import config


@dataclass
class Topic:
    id: int
    topic: str
    status: str
    created_at: str


class TopicQueue:
    def __init__(self, db_path: Path = config.MEMORY_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.db_path, timeout=config.DB_BUSY_TIMEOUT_SECONDS
        )
        # WAL lets the CLI queue topics while the daemon is mid-write.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def add(self, topic: str) -> bool:
        """Add a topic if it isn't already queued/done and the queue has room.
        Returns True if it was added."""
        topic = topic.strip()
        if not topic:
            return False
        if self.pending_count() >= config.CURIOSITY_MAX_QUEUE_SIZE:
            return False
        try:
            self._conn.execute(
                "INSERT INTO topics (topic, status, created_at) VALUES (?, 'pending', ?)",
                (topic, datetime.now(timezone.utc).isoformat()),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False  # already seen this exact topic before

    def pop_next(self) -> Optional[Topic]:
        row = self._conn.execute(
            "SELECT id, topic, status, created_at FROM topics "
            "WHERE status = 'pending' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        self._conn.execute("UPDATE topics SET status = 'done' WHERE id = ?", (row[0],))
        self._conn.commit()
        return Topic(id=row[0], topic=row[1], status="done", created_at=row[3])

    def pending_count(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM topics WHERE status = 'pending'"
        ).fetchone()[0]

    def pending(self, limit: int = 20) -> List[Topic]:
        rows = self._conn.execute(
            "SELECT id, topic, status, created_at FROM topics "
            "WHERE status = 'pending' ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [Topic(id=r[0], topic=r[1], status=r[2], created_at=r[3]) for r in rows]
