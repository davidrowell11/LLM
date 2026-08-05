"""A small local vector store: SQLite for storage, cosine similarity for search.

This is intentionally simple rather than fast — it's built for hundreds to
low thousands of notes, which is what a single person's Chromebook assistant
is expected to accumulate. It loads all embeddings into memory on each
search, which would need to change if this ever needed to scale further.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np

from . import config
from . import llm_client

EmbedFn = Callable[[str], List[float]]


@dataclass
class Note:
    id: int
    topic: str
    source_url: str
    content: str
    created_at: str
    score: Optional[float] = None


class Memory:
    def __init__(self, db_path: Path = config.MEMORY_DB_PATH, embed_fn: EmbedFn = llm_client.embed):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._embed = embed_fn
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                source_url TEXT,
                content TEXT NOT NULL,
                embedding TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def add(self, topic: str, content: str, source_url: str = "") -> int:
        embedding = self._embed(content)
        created_at = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            "INSERT INTO notes (topic, source_url, content, embedding, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (topic, source_url, content, json.dumps(embedding), created_at),
        )
        self._conn.commit()
        return cursor.lastrowid

    def search(self, query: str, top_k: int = config.MEMORY_TOP_K) -> List[Note]:
        rows = self._conn.execute(
            "SELECT id, topic, source_url, content, embedding, created_at FROM notes"
        ).fetchall()
        if not rows:
            return []

        query_vec = np.array(self._embed(query), dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return []

        scored: List[Note] = []
        for row_id, topic, source_url, content, embedding_json, created_at in rows:
            vec = np.array(json.loads(embedding_json), dtype=np.float32)
            vec_norm = np.linalg.norm(vec)
            if vec_norm == 0:
                continue
            score = float(np.dot(query_vec, vec) / (query_norm * vec_norm))
            scored.append(
                Note(
                    id=row_id,
                    topic=topic,
                    source_url=source_url,
                    content=content,
                    created_at=created_at,
                    score=score,
                )
            )

        scored.sort(key=lambda n: n.score, reverse=True)
        return scored[:top_k]

    def recent(self, limit: int = 5) -> List[Note]:
        rows = self._conn.execute(
            "SELECT id, topic, source_url, content, created_at FROM notes "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            Note(id=r[0], topic=r[1], source_url=r[2], content=r[3], created_at=r[4])
            for r in rows
        ]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
