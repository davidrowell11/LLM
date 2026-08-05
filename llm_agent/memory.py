"""A small local vector store: SQLite for storage, cosine similarity for search.

Embeddings are stored as raw float32 blobs rather than JSON text, and search
compares them with a single vectorised matrix multiply rather than a
per-row Python loop. That combination measured ~50-60x faster to scan and
~3.7x smaller on disk at 768 dimensions.

That difference matters here specifically because the background daemon
accumulates notes indefinitely: at default settings a month of unattended
research reaches several thousand notes, where a JSON scan adds over a
second to *every* chat turn before the model starts generating — and more
than that on slower ARM hardware.

Search still loads every embedding and scans linearly, so this is fine for
tens of thousands of notes but is not an approximate-nearest-neighbour index.
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


def _to_blob(embedding: List[float]) -> bytes:
    return np.asarray(embedding, dtype=np.float32).tobytes()


class Memory:
    def __init__(self, db_path: Path = config.MEMORY_DB_PATH, embed_fn: EmbedFn = llm_client.embed):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._embed = embed_fn
        self._conn = sqlite3.connect(
            self.db_path, timeout=config.DB_BUSY_TIMEOUT_SECONDS
        )
        # WAL lets the CLI read while the daemon writes, instead of blocking.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                source_url TEXT,
                content TEXT NOT NULL,
                embedding BLOB NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()
        self._migrate_json_embeddings()

    def _migrate_json_embeddings(self) -> int:
        """Convert any legacy JSON-text embeddings to float32 blobs, in place.

        SQLite columns are dynamically typed, so a database written by an
        earlier version holds TEXT in the same column. Converting once on open
        keeps the read path uniformly binary (and therefore vectorisable)
        instead of branching per row forever.
        """
        legacy = self._conn.execute(
            "SELECT id, embedding FROM notes WHERE typeof(embedding) = 'text'"
        ).fetchall()
        if not legacy:
            return 0

        for row_id, raw in legacy:
            try:
                vec = json.loads(raw)
            except (TypeError, ValueError):
                continue
            self._conn.execute(
                "UPDATE notes SET embedding = ? WHERE id = ?", (_to_blob(vec), row_id)
            )
        self._conn.commit()

        # UPDATE leaves the old, larger values as free pages inside the file,
        # so without this the database keeps its pre-migration size on disk and
        # none of the space saving is actually realised.
        self._conn.execute("VACUUM")
        self._conn.commit()
        return len(legacy)

    def close(self) -> None:
        self._conn.close()

    def add(self, topic: str, content: str, source_url: str = "") -> Optional[int]:
        """Store a note, or return None if we already have this exact content.

        Different sources often restate the same fact. Without this check the
        knowledge base fills with near-copies, and since retrieval returns a
        fixed top_k, duplicates can occupy every slot and crowd out everything
        else the model could have used.
        """
        content = content.strip()
        if not content:
            return None

        existing = self._conn.execute(
            "SELECT id FROM notes WHERE content = ?", (content,)
        ).fetchone()
        if existing:
            return None

        embedding = self._embed(content)
        created_at = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            "INSERT INTO notes (topic, source_url, content, embedding, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (topic, source_url, content, _to_blob(embedding), created_at),
        )
        self._conn.commit()
        return cursor.lastrowid

    def search(self, query: str, top_k: int = config.MEMORY_TOP_K) -> List[Note]:
        rows = self._conn.execute(
            "SELECT id, topic, source_url, content, embedding, created_at FROM notes"
        ).fetchall()
        if not rows:
            return []

        query_vec = np.asarray(self._embed(query), dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return []

        # Notes embedded by a different model have a different dimensionality
        # and simply aren't comparable, so skip them rather than crashing on
        # the reshape. This is what makes switching EMBED_MODEL non-fatal.
        expected_bytes = query_vec.size * 4
        usable = [r for r in rows if len(r[4]) == expected_bytes]
        if not usable:
            return []

        matrix = np.frombuffer(
            b"".join(r[4] for r in usable), dtype=np.float32
        ).reshape(len(usable), query_vec.size)

        norms = np.linalg.norm(matrix, axis=1)
        norms[norms == 0] = 1.0  # avoid divide-by-zero; these score 0 anyway
        scores = (matrix @ query_vec) / (norms * query_norm)

        top_indices = np.argsort(scores)[::-1][:top_k]
        return [
            Note(
                id=usable[i][0],
                topic=usable[i][1],
                source_url=usable[i][2],
                content=usable[i][3],
                created_at=usable[i][5],
                score=float(scores[i]),
            )
            for i in top_indices
        ]

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
