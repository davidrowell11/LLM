"""Tests for embedding storage: blob format, legacy migration, dimension changes."""

import json
import sqlite3

import numpy as np

from llm_agent.memory import Memory

from .conftest import fake_embed


def make_memory(tmp_path, embed_fn=fake_embed):
    return Memory(db_path=tmp_path / "test.db", embed_fn=embed_fn)


def test_embeddings_are_stored_as_blobs(tmp_path):
    memory = make_memory(tmp_path)
    memory.add(topic="pets", content="Cats nap.")

    kind = memory._conn.execute("SELECT typeof(embedding) FROM notes").fetchone()[0]
    assert kind == "blob"


def test_roundtrip_preserves_vector(tmp_path):
    memory = make_memory(tmp_path)
    memory.add(topic="pets", content="Cats nap.")

    raw = memory._conn.execute("SELECT embedding FROM notes").fetchone()[0]
    stored = np.frombuffer(raw, dtype=np.float32)

    assert np.allclose(stored, np.array(fake_embed("Cats nap."), dtype=np.float32))


# --- migration from the previous JSON-text format ------------------------


def write_legacy_row(db_path, content, vector):
    """Write a row exactly as the previous JSON-text version would have."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS notes (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               topic TEXT NOT NULL, source_url TEXT, content TEXT NOT NULL,
               embedding TEXT NOT NULL, created_at TEXT NOT NULL)"""
    )
    conn.execute(
        "INSERT INTO notes (topic, source_url, content, embedding, created_at) "
        "VALUES (?,?,?,?,?)",
        ("legacy", "http://old", content, json.dumps(vector), "2026-01-01"),
    )
    conn.commit()
    conn.close()


def test_legacy_json_rows_are_migrated_to_blobs(tmp_path):
    db = tmp_path / "test.db"
    write_legacy_row(db, "Cats are independent.", fake_embed("cat"))

    memory = Memory(db_path=db, embed_fn=fake_embed)

    kinds = [r[0] for r in memory._conn.execute("SELECT typeof(embedding) FROM notes")]
    assert kinds == ["blob"]
    assert memory.count() == 1


def test_search_works_against_migrated_legacy_data(tmp_path):
    db = tmp_path / "test.db"
    write_legacy_row(db, "Cats are independent.", fake_embed("cat"))
    write_legacy_row(db, "Cars need fuel.", fake_embed("car"))

    memory = Memory(db_path=db, embed_fn=fake_embed)
    results = memory.search("tell me about cats", top_k=1)

    assert len(results) == 1
    assert results[0].content == "Cats are independent."


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "test.db"
    write_legacy_row(db, "Cats are independent.", fake_embed("cat"))

    Memory(db_path=db, embed_fn=fake_embed).close()
    memory = Memory(db_path=db, embed_fn=fake_embed)  # reopen, nothing left to do

    assert memory._migrate_json_embeddings() == 0
    assert memory.count() == 1


def test_migration_skips_corrupt_json(tmp_path):
    db = tmp_path / "test.db"
    write_legacy_row(db, "Good note.", fake_embed("cat"))
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO notes (topic, source_url, content, embedding, created_at) "
        "VALUES ('t','u','Bad note.','not-valid-json','2026-01-01')"
    )
    conn.commit()
    conn.close()

    memory = Memory(db_path=db, embed_fn=fake_embed)  # must not raise

    # The good row still works; the corrupt one is simply not comparable.
    assert [n.content for n in memory.search("cats", top_k=5)] == ["Good note."]


# --- changing embedding model -------------------------------------------


def test_notes_from_a_different_embedding_model_are_skipped(tmp_path):
    """Switching EMBED_MODEL changes dimensionality; old notes must not crash it."""
    memory = make_memory(tmp_path)
    memory.add(topic="pets", content="Cats nap.")  # 3-dim vector
    memory.close()

    def bigger_embed(text):
        return [0.5] * 8  # a different model, 8 dims

    memory = Memory(db_path=tmp_path / "test.db", embed_fn=bigger_embed)
    memory.add(topic="pets", content="Dogs bark.")  # 8-dim vector

    results = memory.search("anything", top_k=5)

    assert [n.content for n in results] == ["Dogs bark."]


def test_search_returns_empty_when_no_note_matches_query_dimension(tmp_path):
    memory = make_memory(tmp_path)
    memory.add(topic="pets", content="Cats nap.")
    memory.close()

    memory = Memory(db_path=tmp_path / "test.db", embed_fn=lambda t: [0.5] * 8)
    assert memory.search("anything") == []


# --- correctness of the vectorised scan ---------------------------------


def test_vectorised_search_ranks_by_cosine_similarity(tmp_path):
    """Ordering must match a straightforward per-row cosine computation."""
    vectors = {
        "a": [1.0, 0.0, 0.0],
        "b": [0.9, 0.1, 0.0],
        "c": [0.0, 1.0, 0.0],
        "d": [0.0, 0.0, 1.0],
    }
    memory = Memory(db_path=tmp_path / "test.db", embed_fn=lambda t: vectors[t[0]])
    for name in vectors:
        memory.add(topic=name, content=f"{name} note")

    query = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    expected = sorted(
        vectors,
        key=lambda k: -float(
            np.dot(query, np.array(vectors[k], dtype=np.float32))
            / (np.linalg.norm(query) * np.linalg.norm(vectors[k]))
        ),
    )

    memory._embed = lambda t: [1.0, 0.0, 0.0]
    got = [n.content[0] for n in memory.search("query", top_k=4)]

    assert got == expected


def test_scores_are_descending(tmp_path):
    memory = make_memory(tmp_path)
    for content in ("Cats nap.", "Dogs bark.", "Cars drive."):
        memory.add(topic="t", content=content)

    scores = [n.score for n in memory.search("cats", top_k=3)]

    assert scores == sorted(scores, reverse=True)
