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
    """Return an embedding vector for the given text."""
    try:
        response = requests.post(
            f"{config.OLLAMA_HOST}/api/embeddings",
            json={"model": model, "prompt": text},
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise OllamaError(
            f"Could not reach Ollama at {config.OLLAMA_HOST} for embeddings. Is "
            f"'{model}' pulled? ({exc})"
        ) from exc

    data = response.json()
    embedding = data.get("embedding")
    if not embedding:
        raise OllamaError(f"Ollama returned no embedding for model '{model}'.")
    return embedding
