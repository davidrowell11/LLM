"""Persistent chat sessions, so the app can hold several separate conversations.

Lives in the same SQLite file as notes and topics. Conversations are distinct
from memory: memory is what Cortana has learned and is shared across every
chat, while a conversation is just one thread of dialogue.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from . import config

TITLE_MAX_CHARS = 42


@dataclass
class Conversation:
    id: int
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0


@dataclass
class Message:
    role: str  # "user" | "assistant"
    content: str
    researched: str = ""


def derive_title(text: str) -> str:
    """Name a conversation after its opening message, the way chat apps do."""
    cleaned = " ".join(text.split())
    if not cleaned:
        return "New chat"
    if len(cleaned) <= TITLE_MAX_CHARS:
        return cleaned
    # Prefer cutting at a word boundary rather than mid-word.
    clipped = cleaned[:TITLE_MAX_CHARS].rsplit(" ", 1)[0]
    return (clipped or cleaned[:TITLE_MAX_CHARS]) + "…"


class ConversationStore:
    def __init__(self, db_path: Path = config.MEMORY_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.db_path, timeout=config.DB_BUSY_TIMEOUT_SECONDS
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL
                    REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                researched TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_conversation "
            "ON messages(conversation_id, id)"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(self, title: str = "New chat") -> int:
        now = self._now()
        cursor = self._conn.execute(
            "INSERT INTO conversations (title, created_at, updated_at) VALUES (?,?,?)",
            (title, now, now),
        )
        self._conn.commit()
        return cursor.lastrowid

    def rename(self, conversation_id: int, title: str) -> None:
        self._conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id)
        )
        self._conn.commit()

    def delete(self, conversation_id: int) -> None:
        # Delete messages explicitly: foreign_keys is per-connection, and this
        # database is opened by several processes that may not enable it.
        self._conn.execute(
            "DELETE FROM messages WHERE conversation_id = ?", (conversation_id,)
        )
        self._conn.execute(
            "DELETE FROM conversations WHERE id = ?", (conversation_id,)
        )
        self._conn.commit()

    def add_message(
        self, conversation_id: int, role: str, content: str, researched: str = ""
    ) -> None:
        now = self._now()
        self._conn.execute(
            "INSERT INTO messages (conversation_id, role, content, researched, "
            "created_at) VALUES (?,?,?,?,?)",
            (conversation_id, role, content, researched, now),
        )
        self._conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
        self._conn.commit()

        # The first user message names an untitled conversation.
        if role == "user":
            row = self._conn.execute(
                "SELECT title FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if row and row[0] == "New chat":
                self.rename(conversation_id, derive_title(content))

    def messages(self, conversation_id: int) -> List[Message]:
        rows = self._conn.execute(
            "SELECT role, content, researched FROM messages "
            "WHERE conversation_id = ? ORDER BY id ASC",
            (conversation_id,),
        ).fetchall()
        return [Message(role=r[0], content=r[1], researched=r[2]) for r in rows]

    def history(self, conversation_id: int, max_turns: int = None) -> List[Dict[str, str]]:
        """Messages in the shape the model expects, capped to recent turns."""
        max_turns = max_turns or config.MAX_HISTORY_TURNS
        msgs = self.messages(conversation_id)
        trimmed = msgs[-max_turns * 2:] if max_turns else msgs
        return [{"role": m.role, "content": m.content} for m in trimmed]

    def list(self, limit: int = 100) -> List[Conversation]:
        rows = self._conn.execute(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id)
            FROM conversations c
            ORDER BY c.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            Conversation(
                id=r[0], title=r[1], created_at=r[2], updated_at=r[3], message_count=r[4]
            )
            for r in rows
        ]

    def most_recent(self) -> Optional[Conversation]:
        found = self.list(limit=1)
        return found[0] if found else None
