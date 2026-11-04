"""Thin wrapper around a local Ollama server's chat and embeddings endpoints."""

from typing import List, Dict

import requests

from . import config


class OllamaError(RuntimeError):
    pass


def chat(messages: List[Dict[str, str]], model: str = config.CHAT_MODEL) -> str:
    """Send a chat-formatted message list to Ollama and return the reply text."""
    try:
        response = requests.post(
            f"{config.OLLAMA_HOST}/api/chat",
            json={"model": model, "messages": messages, "stream": False},
            timeout=config.REQUEST_TIMEOUT_SECONDS * 6,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise OllamaError(
            f"Could not reach Ollama at {config.OLLAMA_HOST}. Is 'ollama serve' running "
            f"and has '{model}' been pulled? ({exc})"
        ) from exc

    data = response.json()
    return data.get("message", {}).get("content", "").strip()


def embed(text: str, model: str = config.EMBED_MODEL) -> List[float]:
    """Return an embedding vector for the given text.

    Uses /api/embed, the current endpoint, and falls back to the legacy
    /api/embeddings only if the server is too old to have it (404). The new
    endpoint returns unit-length vectors while the old one didn't, but memory
    search is cosine similarity, so notes embedded either way rank together.
    """
    try:
        response = requests.post(
            f"{config.OLLAMA_HOST}/api/embed",
            json={"model": model, "input": text},
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code == 404 and "not found" not in _error_text(response):
            return _legacy_embed(text, model)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise _embed_error(model, exc) from exc

    embeddings = response.json().get("embeddings") or []
    if not embeddings or not embeddings[0]:
        raise OllamaError(f"Ollama returned no embedding for model '{model}'.")
    return embeddings[0]


def _error_text(response: requests.Response) -> str:
    """The "error" field of an Ollama error body, lowercased, or "" if absent.

    A 404 from /api/embed means one of two things: the server predates the
    endpoint (plain-text "404 page not found" -- no JSON), or the model isn't
    pulled (JSON error naming the model). Only the first should fall back.
    """
    try:
        return str(response.json().get("error", "")).lower()
    except ValueError:
        return ""


def _legacy_embed(text: str, model: str) -> List[float]:
    """Embeddings via /api/embeddings, for Ollama servers older than 0.3."""
    try:
        response = requests.post(
            f"{config.OLLAMA_HOST}/api/embeddings",
            json={"model": model, "prompt": text},
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise _embed_error(model, exc) from exc

    embedding = response.json().get("embedding")
    if not embedding:
        raise OllamaError(f"Ollama returned no embedding for model '{model}'.")
    return embedding


def _embed_error(model: str, exc: Exception) -> OllamaError:
    return OllamaError(
        f"Could not reach Ollama at {config.OLLAMA_HOST} for embeddings. Is "
        f"'{model}' pulled? ({exc})"
    )
