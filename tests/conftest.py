import pytest

from llm_agent.memory import Memory


def fake_embed(text: str):
    """Toy embedding: one axis per keyword, so similarity is predictable."""
    text = text.lower()
    vec = [
        1.0 if "cat" in text else 0.0,
        1.0 if "dog" in text else 0.0,
        1.0 if "car" in text else 0.0,
    ]
    if vec == [0.0, 0.0, 0.0]:
        vec = [0.1, 0.1, 0.1]
    return vec


@pytest.fixture
def memory(tmp_path):
    mem = Memory(db_path=tmp_path / "test.db", embed_fn=fake_embed)
    yield mem
    mem.close()
