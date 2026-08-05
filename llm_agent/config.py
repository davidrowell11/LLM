"""Environment-variable driven settings. Everything has a Chromebook-friendly default."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "llama3.2:3b")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")

MEMORY_DB_PATH = Path(os.environ.get("MEMORY_DB_PATH", REPO_ROOT / "data" / "memory.db"))
MEMORY_TOP_K = int(os.environ.get("MEMORY_TOP_K", "4"))

SEARCH_RESULTS = int(os.environ.get("SEARCH_RESULTS", "4"))
MAX_FETCH_CHARS = int(os.environ.get("MAX_FETCH_CHARS", "6000"))

REQUEST_TIMEOUT_SECONDS = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "20"))

# Autonomous background research (see daemon.py)
CURIOSITY_INTERVAL_SECONDS = int(os.environ.get("CURIOSITY_INTERVAL_SECONDS", "1800"))
CURIOSITY_MAX_QUEUE_SIZE = int(os.environ.get("CURIOSITY_MAX_QUEUE_SIZE", "50"))
CURIOSITY_FOLLOW_UPS_PER_TOPIC = int(os.environ.get("CURIOSITY_FOLLOW_UPS_PER_TOPIC", "2"))
