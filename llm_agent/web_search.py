"""Web search and page-text extraction, with no API key required.

Search goes through DuckDuckGo's HTML endpoint (the same one you get with
JavaScript disabled), which doesn't require an API key or account. It's
best-effort scraping, not a stable API, so it may need updating if
DuckDuckGo changes their markup.

Parsing uses BeautifulSoup's stdlib "html.parser" rather than lxml on
purpose: lxml is a compiled C extension that needs libxml2/libxslt headers
and a toolchain to build when no prebuilt wheel matches, which is a common
failure on ARM Chromebooks. html.parser is slower but always available.
"""

import re
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from . import config

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT}

SEARCH_URL = "https://html.duckduckgo.com/html/"


@dataclass
class SearchResult:
    title: str
    url: str


def _unwrap_ddg_redirect(href: str) -> str:
    """DuckDuckGo's HTML results wrap links in a redirect; pull the real URL out."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path == "/l/":
        qs = parse_qs(parsed.query)
        if "uddg" in qs:
            return qs["uddg"][0]
    return href


def search(query: str, num_results: int = config.SEARCH_RESULTS) -> List[SearchResult]:
    try:
        response = requests.post(
            SEARCH_URL,
            data={"q": query},
            headers=HEADERS,
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException:
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    results: List[SearchResult] = []
    for anchor in soup.select("a.result__a"):
        title = anchor.get_text(strip=True)
        href = anchor.get("href", "")
        url = _unwrap_ddg_redirect(href)
        if title and url.startswith("http"):
            results.append(SearchResult(title=title, url=url))
        if len(results) >= num_results:
            break
    return results


def fetch_text(url: str, max_chars: int = config.MAX_FETCH_CHARS) -> Optional[str]:
    """Fetch a page and return its readable text, or None if it couldn't be fetched."""
    try:
        response = requests.get(
            url, headers=HEADERS, timeout=config.REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
    except requests.RequestException:
        return None

    content_type = response.headers.get("Content-Type", "")
    if "html" not in content_type:
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return None
    return text[:max_chars]
