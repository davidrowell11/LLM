"""Tests for the GUI's background worker protocol.

Skipped where Tk isn't installed (gui.py imports tkinter at module scope), so
the suite still runs on a headless machine without python3-tk.
"""

import queue

import pytest

pytest.importorskip("tkinter", reason="python3-tk not installed")

from llm_agent import gui  # noqa: E402
from llm_agent.agent import ChatResult, LearnResult  # noqa: E402
from llm_agent.llm_client import OllamaError  # noqa: E402


class StubAgent:
    def __init__(self, chat_result=None, learn_result=None, error=None):
        self.chat_result = chat_result
        self.learn_result = learn_result
        self.error = error

    def chat(self, message, history=None):
        if self.error:
            raise self.error
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


def run_worker(monkeypatch, agent, requests):
    """Run the worker over a fixed request list and collect its responses."""
    monkeypatch.setattr(gui, "Memory", lambda *a, **k: StubMemory())
    monkeypatch.setattr(gui, "TopicQueue", lambda *a, **k: StubTopics())
    monkeypatch.setattr(gui, "Agent", lambda *a, **k: agent)

    req_q: "queue.Queue" = queue.Queue()
    res_q: "queue.Queue" = queue.Queue()
    for request in requests:
        req_q.put(request)
    req_q.put(None)  # shutdown sentinel

    gui.Worker(req_q, res_q).run()  # run inline, no thread needed

    out = []
    while not res_q.empty():
        out.append(res_q.get())
    return out


def test_worker_reports_chat_answer(monkeypatch):
    agent = StubAgent(
        chat_result=ChatResult(answer="Hello.", researched_query="", notes_added=0)
    )
    responses = run_worker(monkeypatch, agent, [gui.Request(kind="chat", payload="hi")])

    chats = [r for r in responses if r.kind == "chat"]
    assert len(chats) == 1
    assert chats[0].text == "Hello."


def test_worker_surfaces_auto_research(monkeypatch):
    agent = StubAgent(
        chat_result=ChatResult(answer="Done.", researched_query="cats", notes_added=3)
    )
    responses = run_worker(monkeypatch, agent, [gui.Request(kind="chat", payload="hi")])

    chat = next(r for r in responses if r.kind == "chat")
    assert chat.researched == "cats"
    assert chat.notes_added == 3


def test_worker_emits_stats(monkeypatch):
    agent = StubAgent(chat_result=ChatResult(answer="ok"))
    responses = run_worker(monkeypatch, agent, [])

    stats = [r for r in responses if r.kind == "stats"]
    assert stats and stats[0].note_count == 7 and stats[0].pending == 2


def test_worker_turns_ollama_errors_into_messages(monkeypatch):
    agent = StubAgent(error=OllamaError("ollama is down"))
    responses = run_worker(monkeypatch, agent, [gui.Request(kind="chat", payload="hi")])

    errors = [r for r in responses if r.kind == "error"]
    assert errors and "ollama is down" in errors[0].error


def test_worker_reports_unreachable_web_on_learn(monkeypatch):
    agent = StubAgent(learn_result=LearnResult(topic="cats", reachable=False))
    responses = run_worker(
        monkeypatch, agent, [gui.Request(kind="learn", payload="cats")]
    )

    notice = next(r for r in responses if r.kind == "notice")
    assert "Couldn't reach the web" in notice.text


def test_worker_reports_learned_notes(monkeypatch):
    agent = StubAgent(
        learn_result=LearnResult(topic="cats", notes_added=[("http://a", "n")])
    )
    responses = run_worker(
        monkeypatch, agent, [gui.Request(kind="learn", payload="cats")]
    )

    notice = next(r for r in responses if r.kind == "notice")
    assert "Learned 1 note(s)" in notice.text
