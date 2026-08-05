from llm_agent.topics import TopicQueue


def make_queue(tmp_path):
    return TopicQueue(db_path=tmp_path / "test.db")


def test_add_and_pop_in_order(tmp_path):
    queue = make_queue(tmp_path)
    assert queue.add("cats")
    assert queue.add("dogs")

    first = queue.pop_next()
    second = queue.pop_next()

    assert first.topic == "cats"
    assert second.topic == "dogs"
    assert queue.pop_next() is None


def test_add_rejects_duplicates(tmp_path):
    queue = make_queue(tmp_path)
    assert queue.add("cats")
    assert not queue.add("cats")
    assert queue.pending_count() == 1


def test_add_rejects_when_queue_full(tmp_path, monkeypatch):
    import llm_agent.config as config

    monkeypatch.setattr(config, "CURIOSITY_MAX_QUEUE_SIZE", 1)
    queue = make_queue(tmp_path)
    assert queue.add("cats")
    assert not queue.add("dogs")


def test_pending_count_excludes_done(tmp_path):
    queue = make_queue(tmp_path)
    queue.add("cats")
    queue.add("dogs")
    queue.pop_next()

    assert queue.pending_count() == 1
