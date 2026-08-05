"""Tests for persistent chat sessions."""

import pytest

from llm_agent.conversations import ConversationStore, derive_title


@pytest.fixture
def store(tmp_path):
    s = ConversationStore(db_path=tmp_path / "test.db")
    yield s
    s.close()


# --- titles -------------------------------------------------------------


def test_title_from_short_message():
    assert derive_title("What is a chinchilla?") == "What is a chinchilla?"


def test_title_truncates_on_a_word_boundary():
    title = derive_title(
        "Explain in detail how retrieval augmented generation actually works "
        "under the hood"
    )
    assert len(title) <= 43
    assert title.endswith("…")
    assert not title.rstrip("…").endswith(" ")


def test_title_collapses_whitespace():
    assert derive_title("  hello \n  world  ") == "hello world"


def test_title_falls_back_when_empty():
    assert derive_title("   ") == "New chat"


# --- lifecycle ----------------------------------------------------------


def test_create_and_list(store):
    store.create()
    store.create()
    assert len(store.list()) == 2


def test_new_chat_is_titled_by_first_user_message(store):
    cid = store.create()
    store.add_message(cid, "user", "How do Chromebooks handle Linux?")

    assert store.list()[0].title == "How do Chromebooks handle Linux?"


def test_later_messages_do_not_rename(store):
    cid = store.create()
    store.add_message(cid, "user", "First question")
    store.add_message(cid, "assistant", "An answer")
    store.add_message(cid, "user", "Second question")

    assert store.list()[0].title == "First question"


def test_messages_round_trip_in_order(store):
    cid = store.create()
    store.add_message(cid, "user", "hi")
    store.add_message(cid, "assistant", "hello", researched="greetings")

    msgs = store.messages(cid)
    assert [(m.role, m.content) for m in msgs] == [
        ("user", "hi"), ("assistant", "hello")
    ]
    assert msgs[1].researched == "greetings"


def test_conversations_are_isolated(store):
    a, b = store.create(), store.create()
    store.add_message(a, "user", "in A")
    store.add_message(b, "user", "in B")

    assert [m.content for m in store.messages(a)] == ["in A"]
    assert [m.content for m in store.messages(b)] == ["in B"]


def test_most_recent_reflects_latest_activity(store):
    first = store.create()
    store.create()  # newer, but with no activity
    store.add_message(first, "user", "later activity in the older chat")

    assert store.most_recent().id == first


# --- deletion -----------------------------------------------------------


def test_delete_removes_conversation(store):
    cid = store.create()
    store.add_message(cid, "user", "hi")

    store.delete(cid)

    assert store.list() == []


def test_delete_removes_its_messages(store):
    cid = store.create()
    store.add_message(cid, "user", "hi")
    store.delete(cid)

    assert store.messages(cid) == []


def test_delete_leaves_other_conversations_alone(store):
    keep, drop = store.create(), store.create()
    store.add_message(keep, "user", "keep me")
    store.add_message(drop, "user", "delete me")

    store.delete(drop)

    assert [c.id for c in store.list()] == [keep]
    assert [m.content for m in store.messages(keep)] == ["keep me"]


def test_rename(store):
    cid = store.create()
    store.rename(cid, "Renamed")
    assert store.list()[0].title == "Renamed"


# --- history for the model ----------------------------------------------


def test_history_is_in_model_format(store):
    cid = store.create()
    store.add_message(cid, "user", "hi")
    store.add_message(cid, "assistant", "hello")

    assert store.history(cid) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_history_is_capped_to_recent_turns(store):
    cid = store.create()
    for i in range(20):
        store.add_message(cid, "user", f"q{i}")
        store.add_message(cid, "assistant", f"a{i}")

    history = store.history(cid, max_turns=3)

    assert len(history) == 6
    assert history[-1]["content"] == "a19"


def test_history_survives_reopening_the_store(tmp_path):
    """A conversation resumed after a restart must keep its context."""
    db = tmp_path / "test.db"
    first = ConversationStore(db_path=db)
    cid = first.create()
    first.add_message(cid, "user", "remember this")
    first.close()

    second = ConversationStore(db_path=db)
    assert [m.content for m in second.messages(cid)] == ["remember this"]
    second.close()


def test_message_count_is_reported(store):
    cid = store.create()
    store.add_message(cid, "user", "one")
    store.add_message(cid, "assistant", "two")

    assert store.list()[0].message_count == 2
