"""Tests for the chat/research orchestration, with the LLM and network mocked out."""

import pytest

from llm_agent import agent as agent_module
from llm_agent.agent import Agent, _extract_search_query, _strip_search_directives
from llm_agent.web_search import SearchResult


class FakeLLM:
    """Returns queued replies in order, recording the prompts it was given."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, messages, model=None):
        self.calls.append(messages)
        return self.replies.pop(0) if self.replies else ""


@pytest.fixture
def patched(monkeypatch):
    """Patch out the LLM and the web so tests are hermetic."""

    def _apply(replies, search_results=None, page_text="Some page content."):
        fake = FakeLLM(replies)
        monkeypatch.setattr(agent_module.llm_client, "chat", fake)
        monkeypatch.setattr(
            agent_module.web_search,
            "search",
            lambda q, num_results=4: list(search_results or []),
        )
        monkeypatch.setattr(
            agent_module.web_search, "fetch_text", lambda url, max_chars=6000: page_text
        )
        return fake

    return _apply


# --- directive parsing --------------------------------------------------


def test_extract_query_from_bare_directive():
    assert _extract_search_query("SEARCH: python release date") == "python release date"


def test_extract_query_when_model_is_chatty():
    """The bug this regression-tests: small models prepend filler text."""
    reply = "Sure, let me look that up.\nSEARCH: python release date"
    assert _extract_search_query(reply) == "python release date"


def test_extract_query_with_trailing_text():
    reply = "SEARCH: python release date\n\nI'll check that for you."
    assert _extract_search_query(reply) == "python release date"


def test_no_directive_returns_empty():
    assert _extract_search_query("Paris is the capital of France.") == ""


def test_absurdly_long_query_is_rejected():
    reply = "SEARCH: " + "x" * 500
    assert _extract_search_query(reply) == ""


def test_strip_removes_leaked_directives():
    text = "Here's the answer.\nSEARCH: something else\nMore answer."
    assert "SEARCH:" not in _strip_search_directives(text)


# --- chat flow ----------------------------------------------------------


def test_chat_without_directive_returns_reply(patched, memory):
    patched(["Paris is the capital of France."])
    result = Agent(memory=memory).chat("What is the capital of France?")

    assert result.answer == "Paris is the capital of France."
    assert result.researched_query == ""
    assert result.notes_added == 0


def test_chat_triggers_research_on_chatty_directive(patched, memory):
    fake = patched(
        replies=[
            "Let me look that up.\nSEARCH: cat behaviour",  # chatty directive
            "A note about cats.",  # summarizing the fetched page
            "Cats are independent, based on what I just read.",  # final answer
        ],
        search_results=[SearchResult(title="Cats", url="http://cats.example")],
    )
    result = Agent(memory=memory).chat("Tell me about cats")

    assert result.researched_query == "cat behaviour"
    assert result.notes_added == 1
    assert result.answer == "Cats are independent, based on what I just read."
    assert memory.count() == 1
    assert len(fake.calls) == 3


def test_chat_strips_directive_leaked_into_final_answer(patched, memory):
    patched(
        replies=[
            "SEARCH: cat behaviour",
            "A note about cats.",
            "Cats are independent.\nSEARCH: more cat facts",
        ],
        search_results=[SearchResult(title="Cats", url="http://cats.example")],
    )
    result = Agent(memory=memory).chat("Tell me about cats")

    assert "SEARCH:" not in result.answer
    assert result.answer == "Cats are independent."


def test_chat_falls_back_when_research_finds_nothing(patched, memory):
    patched(
        replies=["SEARCH: obscure topic", ""],
        search_results=[],  # no search hits at all
    )
    result = Agent(memory=memory).chat("Tell me about something obscure")

    assert result.notes_added == 0
    assert "couldn't find anything usable" in result.answer
    assert "SEARCH:" not in result.answer


def test_chat_only_researches_once_per_turn(patched, memory):
    """A model that keeps asking to search must not loop."""
    fake = patched(
        replies=[
            "SEARCH: cats",
            "A note about cats.",
            "SEARCH: cats again",  # tries to search a second time
        ],
        search_results=[SearchResult(title="Cats", url="http://cats.example")],
    )
    result = Agent(memory=memory).chat("Tell me about cats")

    # Exactly one research round: initial + 1 summary + final = 3 LLM calls.
    assert len(fake.calls) == 3
    assert "SEARCH:" not in result.answer


def test_chat_passes_memory_context_to_model(patched, memory):
    memory.add(topic="pets", content="Cats sleep a lot.", source_url="http://c")
    fake = patched(["They sleep a lot."])
    Agent(memory=memory).chat("Tell me about cats")

    system_text = " ".join(
        m["content"] for m in fake.calls[0] if m["role"] == "system"
    )
    assert "Cats sleep a lot." in system_text


def test_chat_system_prompt_uses_assistant_name(patched, memory):
    fake = patched(["Hello."])
    Agent(memory=memory).chat("hi")

    system_text = " ".join(
        m["content"] for m in fake.calls[0] if m["role"] == "system"
    )
    assert "Cortana" in system_text


# --- learn --------------------------------------------------------------


def test_learn_saves_a_note_per_fetched_page(patched, memory):
    patched(
        replies=["Note one.", "Note two."],
        search_results=[
            SearchResult(title="A", url="http://a.example"),
            SearchResult(title="B", url="http://b.example"),
        ],
    )
    result = Agent(memory=memory).learn("cats")

    assert len(result.notes_added) == 2
    assert memory.count() == 2


def test_learn_does_not_count_duplicate_notes(patched, memory):
    """Two sources restating the same fact should store and count once."""
    patched(
        replies=["Cats are independent.", "Cats are independent."],
        search_results=[
            SearchResult(title="A", url="http://a.example"),
            SearchResult(title="B", url="http://b.example"),
        ],
    )
    result = Agent(memory=memory).learn("cats")

    assert len(result.notes_added) == 1
    assert memory.count() == 1


def test_learn_skips_pages_that_fail_to_fetch(patched, memory, monkeypatch):
    patched(
        replies=["Note one."],
        search_results=[SearchResult(title="A", url="http://a.example")],
    )
    monkeypatch.setattr(
        agent_module.web_search, "fetch_text", lambda url, max_chars=6000: None
    )
    result = Agent(memory=memory).learn("cats")

    assert result.notes_added == []
    assert memory.count() == 0


# --- follow-up topic suggestions ---------------------------------------


def test_suggest_follow_ups_parses_lines(patched, memory):
    patched(["cat nutrition\n- cat sleep cycles"])
    topics = Agent(memory=memory).suggest_follow_up_topics(
        "cats", [("http://a", "a note")]
    )

    assert topics == ["cat nutrition", "cat sleep cycles"]


def test_suggest_follow_ups_honours_none(patched, memory):
    patched(["NONE"])
    assert Agent(memory=memory).suggest_follow_up_topics(
        "cats", [("http://a", "a note")]
    ) == []


def test_suggest_follow_ups_respects_cap(patched, memory):
    patched(["one\ntwo\nthree\nfour\nfive"])
    topics = Agent(memory=memory).suggest_follow_up_topics(
        "cats", [("http://a", "a note")]
    )

    assert len(topics) <= 2  # CURIOSITY_FOLLOW_UPS_PER_TOPIC default


def test_suggest_follow_ups_without_notes_skips_llm(patched, memory):
    fake = patched(["should not be called"])
    assert Agent(memory=memory).suggest_follow_up_topics("cats", []) == []
    assert fake.calls == []
