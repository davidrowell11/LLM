"""Tests for the GUI's background worker protocol.

Skipped where Tk isn't installed (gui.py imports tkinter at module scope), so
the suite still runs on a headless machine without python3-tk.
"""

import queue

import pytest

pytest.importorskip("tkinter", reason="python3-tk not installed")

from llm_agent import gui  # noqa: E402
from llm_agent.agent import ChatResult, LearnResult  # noqa: E402
from llm_agent.conversations import Conversation, Message  # noqa: E402
from llm_agent.llm_client import OllamaError  # noqa: E402


class StubAgent:
    def __init__(self, chat_result=None, learn_result=None, error=None):
        self.chat_result = chat_result
        self.learn_result = learn_result
        self.error = error
        self.history_seen = None

    def chat(self, message, history=None):
        if self.error:
            raise self.error
        self.history_seen = history
        return self.chat_result

    def learn(self, topic):
        if self.error:
            raise self.error
        return self.learn_result


class StubMemory:
    def count(self):
        return 7

    def close(self):
        pass


class StubTopics:
    def __init__(self):
        self.added = []

    def add(self, topic):
        self.added.append(topic)
        return True

    def pending_count(self):
        return 2

    def close(self):
        pass


class StubChats:
    def __init__(self):
        self.messages_by_id = {}
        self.created = 0
        self.deleted = []
        self.renamed = []
        self._history = []

    def most_recent(self):
        return None

    def create(self, title="New chat"):
        self.created += 1
        self.messages_by_id[self.created] = []
        return self.created

    def add_message(self, cid, role, content, researched=""):
        self.messages_by_id.setdefault(cid, []).append(
            Message(role=role, content=content, researched=researched)
        )

    def messages(self, cid):
        return list(self.messages_by_id.get(cid, []))

    def history(self, cid, max_turns=None):
        return [
            {"role": m.role, "content": m.content}
            for m in self.messages_by_id.get(cid, [])
        ]

    def list(self, limit=100):
        return [
            Conversation(id=cid, title=f"chat {cid}", created_at="", updated_at="",
                         message_count=len(msgs))
            for cid, msgs in self.messages_by_id.items()
        ]

    def delete(self, cid):
        self.deleted.append(cid)
        self.messages_by_id.pop(cid, None)

    def rename(self, cid, title):
        self.renamed.append((cid, title))

    def close(self):
        pass


def run_worker(monkeypatch, agent, requests, chats=None):
    """Run the worker over a fixed request list and collect its responses."""
    chats = chats or StubChats()
    monkeypatch.setattr(gui, "Memory", lambda *a, **k: StubMemory())
    monkeypatch.setattr(gui, "TopicQueue", lambda *a, **k: StubTopics())
    monkeypatch.setattr(gui, "Agent", lambda *a, **k: agent)
    monkeypatch.setattr(gui, "ConversationStore", lambda *a, **k: chats)

    req_q: "queue.Queue" = queue.Queue()
    res_q: "queue.Queue" = queue.Queue()
    for request in requests:
        req_q.put(request)
    req_q.put(None)  # shutdown sentinel

    gui.Worker(req_q, res_q).run()  # run inline, no thread needed

    out = []
    while not res_q.empty():
        out.append(res_q.get())
    return out, chats


def chat_request(payload="hi", cid=1):
    return gui.Request(kind="chat", payload=payload, conversation_id=cid)


# --- chat ---------------------------------------------------------------


def test_worker_reports_chat_answer(monkeypatch):
    agent = StubAgent(chat_result=ChatResult(answer="Hello."))
    responses, _ = run_worker(monkeypatch, agent, [chat_request()])

    chats = [r for r in responses if r.kind == "chat"]
    assert len(chats) == 1
    assert chats[0].text == "Hello."
    assert chats[0].clears_busy is True


def test_worker_surfaces_auto_research(monkeypatch):
    agent = StubAgent(
        chat_result=ChatResult(answer="Done.", researched_query="cats", notes_added=3)
    )
    responses, _ = run_worker(monkeypatch, agent, [chat_request()])

    chat = next(r for r in responses if r.kind == "chat")
    assert chat.researched == "cats"
    assert chat.notes_added == 3


def test_worker_persists_both_sides_of_the_exchange(monkeypatch):
    agent = StubAgent(chat_result=ChatResult(answer="An answer."))
    _, store = run_worker(monkeypatch, agent, [chat_request("A question.")])

    assert [(m.role, m.content) for m in store.messages(1)] == [
        ("user", "A question."),
        ("assistant", "An answer."),
    ]


def test_worker_passes_prior_turns_as_history(monkeypatch):
    """Follow-up questions need the earlier exchange for context."""
    store = StubChats()
    store.create()
    store.add_message(1, "user", "first question")
    store.add_message(1, "assistant", "first answer")

    agent = StubAgent(chat_result=ChatResult(answer="second answer"))
    run_worker(monkeypatch, agent, [chat_request("second question")], chats=store)

    assert agent.history_seen == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
    ]


def test_worker_excludes_the_current_message_from_history(monkeypatch):
    agent = StubAgent(chat_result=ChatResult(answer="ok"))
    run_worker(monkeypatch, agent, [chat_request("only message")])

    assert agent.history_seen == []


def test_worker_creates_a_conversation_when_none_given(monkeypatch):
    agent = StubAgent(chat_result=ChatResult(answer="ok"))
    responses, store = run_worker(
        monkeypatch, agent, [gui.Request(kind="chat", payload="hi")]
    )

    assert any(r.kind == "chat" for r in responses)
    assert store.created >= 1


# --- conversation management --------------------------------------------


def test_worker_opens_a_conversation_on_start(monkeypatch):
    agent = StubAgent(chat_result=ChatResult(answer="ok"))
    responses, _ = run_worker(monkeypatch, agent, [])

    opened = [r for r in responses if r.kind == "opened"]
    assert opened and opened[0].conversation_id is not None


def test_worker_creates_new_chat(monkeypatch):
    agent = StubAgent(chat_result=ChatResult(answer="ok"))
    responses, store = run_worker(
        monkeypatch, agent, [gui.Request(kind="new_chat")]
    )

    opened = [r for r in responses if r.kind == "opened"]
    assert len(opened) == 2  # one at startup, one for the new chat
    assert opened[1].messages == []
    assert store.created == 2


def test_worker_deletes_a_chat(monkeypatch):
    agent = StubAgent(chat_result=ChatResult(answer="ok"))
    _, store = run_worker(
        monkeypatch, agent,
        [gui.Request(kind="delete_chat", conversation_id=1)],
    )

    assert 1 in store.deleted


def test_worker_reopens_something_after_deleting(monkeypatch):
    """Deleting the last chat must still leave the UI with one open."""
    agent = StubAgent(chat_result=ChatResult(answer="ok"))
    responses, _ = run_worker(
        monkeypatch, agent,
        [gui.Request(kind="delete_chat", conversation_id=1)],
    )

    assert responses[-2].kind in ("opened", "stats")
    opened = [r for r in responses if r.kind == "opened"]
    assert opened[-1].conversation_id is not None


def test_worker_renames_a_chat(monkeypatch):
    agent = StubAgent(chat_result=ChatResult(answer="ok"))
    _, store = run_worker(
        monkeypatch, agent,
        [gui.Request(kind="rename_chat", payload="New name", conversation_id=1)],
    )

    assert store.renamed == [(1, "New name")]


# --- research and errors ------------------------------------------------


def test_worker_emits_stats(monkeypatch):
    agent = StubAgent(chat_result=ChatResult(answer="ok"))
    responses, _ = run_worker(monkeypatch, agent, [])

    stats = [r for r in responses if r.kind == "stats"]
    assert stats and stats[0].note_count == 7 and stats[0].pending == 2


def test_worker_turns_ollama_errors_into_messages(monkeypatch):
    agent = StubAgent(error=OllamaError("ollama is down"))
    responses, _ = run_worker(monkeypatch, agent, [chat_request()])

    errors = [r for r in responses if r.kind == "error"]
    assert errors and "ollama is down" in errors[0].error
    assert errors[0].clears_busy is True


def test_worker_reports_unreachable_web_on_learn(monkeypatch):
    agent = StubAgent(learn_result=LearnResult(topic="cats", reachable=False))
    responses, _ = run_worker(
        monkeypatch, agent, [gui.Request(kind="learn", payload="cats")]
    )

    notice = next(r for r in responses if r.kind == "notice")
    assert "Couldn't reach the web" in notice.text


def test_worker_reports_learned_notes(monkeypatch):
    agent = StubAgent(
        learn_result=LearnResult(topic="cats", notes_added=[("http://a", "n")])
    )
    responses, _ = run_worker(
        monkeypatch, agent, [gui.Request(kind="learn", payload="cats")]
    )

    notice = next(r for r in responses if r.kind == "notice")
    assert "Learned 1 note(s)" in notice.text


def test_queued_topic_notice_does_not_clear_busy(monkeypatch):
    """Otherwise it would re-enable Send while a chat is still running."""
    agent = StubAgent(chat_result=ChatResult(answer="ok"))
    responses, _ = run_worker(
        monkeypatch, agent, [gui.Request(kind="curious", payload="cats")]
    )

    notice = next(r for r in responses if r.kind == "notice")
    assert notice.clears_busy is False
