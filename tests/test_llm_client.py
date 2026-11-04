"""Tests for the Ollama client's embedding endpoint selection (HTTP stubbed)."""

import pytest
import requests

from llm_agent import llm_client


class FakeResponse:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class CallLog(list):
    """The (endpoint, payload) pairs posted, plus the canned reply per endpoint."""

    def __init__(self):
        super().__init__()
        self.routes = {}


@pytest.fixture
def calls(monkeypatch):
    log = CallLog()

    def fake_post(url, json=None, timeout=None):
        path = url.rsplit("/api/", 1)[1]
        log.append((path, json))
        return log.routes[path]

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    return log


def test_uses_current_embed_endpoint(calls):
    calls.routes["embed"] = FakeResponse(200, {"embeddings": [[0.6, 0.8]]})
    assert llm_client.embed("hello", model="m") == [0.6, 0.8]
    assert calls == [("embed", {"model": "m", "input": "hello"})]


def test_falls_back_on_servers_without_api_embed(calls):
    calls.routes["embed"] = FakeResponse(404, None, "404 page not found")
    calls.routes["embeddings"] = FakeResponse(200, {"embedding": [3.0, 4.0]})
    assert llm_client.embed("hello", model="m") == [3.0, 4.0]
    assert [path for path, _ in calls] == ["embed", "embeddings"]
    assert calls[1][1] == {"model": "m", "prompt": "hello"}


def test_missing_model_is_an_error_not_a_fallback(calls):
    calls.routes["embed"] = FakeResponse(404, {"error": 'model "m" not found, try pulling it first'})
    with pytest.raises(llm_client.OllamaError, match="pulled"):
        llm_client.embed("hello", model="m")
    assert [path for path, _ in calls] == ["embed"]


def test_empty_embedding_is_an_error(calls):
    calls.routes["embed"] = FakeResponse(200, {"embeddings": []})
    with pytest.raises(llm_client.OllamaError, match="no embedding"):
        llm_client.embed("hello", model="m")
