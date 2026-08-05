"""Tests for search-result parsing and page text extraction, with no network access."""

import pytest

from llm_agent import web_search
from llm_agent.web_search import _unwrap_ddg_redirect, fetch_text, search

DDG_HTML = """
<html><body>
  <div class="result">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fcats&amp;rut=abc">
      All About Cats
    </a>
  </div>
  <div class="result">
    <a class="result__a" href="https://direct.example.com/dogs">All About Dogs</a>
  </div>
  <div class="result">
    <a class="result__a" href="/relative/not-http">Should be skipped</a>
  </div>
</body></html>
"""

PAGE_HTML = """
<html><head><title>T</title><style>.x{color:red}</style></head>
<body>
  <nav>Home About Contact</nav>
  <script>console.log("tracking");</script>
  <p>Cats are independent animals.</p>
  <p>They sleep a lot.</p>
  <footer>Copyright 2026</footer>
</body></html>
"""


class FakeResponse:
    def __init__(self, text="", headers=None, status=200):
        self.text = text
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise web_search.requests.HTTPError(f"status {self.status_code}")


# --- redirect unwrapping ------------------------------------------------


def test_unwrap_ddg_redirect_extracts_real_url():
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fcats&rut=abc"
    assert _unwrap_ddg_redirect(href) == "https://example.com/cats"


def test_unwrap_leaves_direct_urls_alone():
    assert _unwrap_ddg_redirect("https://example.com/x") == "https://example.com/x"


def test_unwrap_adds_scheme_to_protocol_relative_url():
    assert _unwrap_ddg_redirect("//example.com/x").startswith("https://")


# --- search -------------------------------------------------------------


def test_search_parses_results(monkeypatch):
    monkeypatch.setattr(
        web_search.requests, "post", lambda *a, **k: FakeResponse(DDG_HTML)
    )
    results = search("cats", num_results=5)

    assert [r.url for r in results] == [
        "https://example.com/cats",
        "https://direct.example.com/dogs",
    ]
    assert results[0].title == "All About Cats"


def test_search_respects_num_results(monkeypatch):
    monkeypatch.setattr(
        web_search.requests, "post", lambda *a, **k: FakeResponse(DDG_HTML)
    )
    assert len(search("cats", num_results=1)) == 1


def test_search_raises_on_network_error(monkeypatch):
    """Must be distinguishable from a search that simply matched nothing."""

    def boom(*a, **k):
        raise web_search.requests.ConnectionError("offline")

    monkeypatch.setattr(web_search.requests, "post", boom)
    with pytest.raises(web_search.SearchError):
        search("cats")


def test_search_returns_empty_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(
        web_search.requests,
        "post",
        lambda *a, **k: FakeResponse("<html><body>no results</body></html>"),
    )
    assert search("cats") == []


# --- fetch_text ---------------------------------------------------------


def test_fetch_text_extracts_readable_content(monkeypatch):
    monkeypatch.setattr(
        web_search.requests, "get", lambda *a, **k: FakeResponse(PAGE_HTML)
    )
    text = fetch_text("http://example.com")

    assert "Cats are independent animals." in text
    assert "tracking" not in text  # script stripped
    assert "color:red" not in text  # style stripped
    assert "Copyright" not in text  # footer stripped


def test_fetch_text_truncates(monkeypatch):
    long_html = "<html><body><p>" + ("word " * 5000) + "</p></body></html>"
    monkeypatch.setattr(
        web_search.requests, "get", lambda *a, **k: FakeResponse(long_html)
    )
    assert len(fetch_text("http://example.com", max_chars=100)) == 100


def test_fetch_text_rejects_non_html(monkeypatch):
    monkeypatch.setattr(
        web_search.requests,
        "get",
        lambda *a, **k: FakeResponse("{}", headers={"Content-Type": "application/json"}),
    )
    assert fetch_text("http://example.com/api") is None


def test_fetch_text_returns_none_on_error(monkeypatch):
    def boom(*a, **k):
        raise web_search.requests.Timeout("too slow")

    monkeypatch.setattr(web_search.requests, "get", boom)
    assert fetch_text("http://example.com") is None
