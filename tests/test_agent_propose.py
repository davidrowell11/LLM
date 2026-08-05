"""Tests for self-proposed research topics (used when the daemon's queue drains)."""

import pytest

from llm_agent import agent as agent_module
from llm_agent.agent import Agent


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, messages, model=None):
        self.calls.append(messages)
        return self.replies.pop(0) if self.replies else ""


@pytest.fixture
def fake_llm(monkeypatch):
    def _apply(replies):
        fake = FakeLLM(replies)
        monkeypatch.setattr(agent_module.llm_client, "chat", fake)
        return fake

    return _apply


def test_propose_returns_nothing_when_memory_is_empty(fake_llm, memory):
    fake = fake_llm(["should not be called"])

    assert Agent(memory=memory).propose_new_topics() == []
    assert fake.calls == []  # no point asking with nothing to build on


def test_propose_suggests_topics_from_memory(fake_llm, memory):
    memory.add(topic="cats", content="Cats are independent.")
    fake_llm(["cat nutrition\ncat breeds"])

    topics = Agent(memory=memory).propose_new_topics()

    assert topics == ["cat nutrition", "cat breeds"]


def test_propose_honours_none(fake_llm, memory):
    memory.add(topic="cats", content="Cats are independent.")
    fake_llm(["NONE"])

    assert Agent(memory=memory).propose_new_topics() == []


def test_propose_respects_limit(fake_llm, memory):
    memory.add(topic="cats", content="Cats are independent.")
    fake_llm(["one\ntwo\nthree\nfour\nfive"])

    assert len(Agent(memory=memory).propose_new_topics(limit=2)) == 2


def test_propose_includes_existing_notes_in_prompt(fake_llm, memory):
    memory.add(topic="cats", content="Cats are independent.")
    fake = fake_llm(["cat nutrition"])

    Agent(memory=memory).propose_new_topics()

    prompt_text = " ".join(m["content"] for m in fake.calls[0])
    assert "Cats are independent." in prompt_text
