"""Unit tests for the local vector store, using a fake embedding function
so these run without Ollama installed."""

from llm_agent.memory import Memory


def fake_embed(text: str):
    """Toy embedding: one axis per keyword, so similarity is predictable in tests."""
    text = text.lower()
    vec = [
        1.0 if "cat" in text else 0.0,
        1.0 if "dog" in text else 0.0,
        1.0 if "car" in text else 0.0,
    ]
    if vec == [0.0, 0.0, 0.0]:
        vec = [0.1, 0.1, 0.1]
    return vec


def make_memory(tmp_path):
    return Memory(db_path=tmp_path / "test.db", embed_fn=fake_embed)


def test_add_and_count(tmp_path):
    memory = make_memory(tmp_path)
    assert memory.count() == 0
    memory.add(topic="pets", content="Cats are independent animals.", source_url="http://a")
    assert memory.count() == 1


def test_search_returns_most_similar(tmp_path):
    memory = make_memory(tmp_path)
    memory.add(topic="pets", content="Cats are independent animals.", source_url="http://cats")
    memory.add(topic="pets", content="Dogs are loyal companions.", source_url="http://dogs")
    memory.add(topic="vehicles", content="Cars need regular maintenance.", source_url="http://cars")

    results = memory.search("Tell me about cats", top_k=1)

    assert len(results) == 1
    assert results[0].source_url == "http://cats"
    assert results[0].score > 0


def test_search_empty_memory_returns_empty_list(tmp_path):
    memory = make_memory(tmp_path)
    assert memory.search("anything") == []


def test_search_respects_top_k(tmp_path):
    memory = make_memory(tmp_path)
    for i in range(5):
        memory.add(topic="dogs", content=f"Dog fact number {i}.", source_url=f"http://dog{i}")

    results = memory.search("dogs", top_k=3)

    assert len(results) == 3


def test_recent_orders_newest_first(tmp_path):
    memory = make_memory(tmp_path)
    memory.add(topic="t", content="first note about cats")
    memory.add(topic="t", content="second note about dogs")

    recent = memory.recent(limit=2)

    assert [n.content for n in recent] == ["second note about dogs", "first note about cats"]
