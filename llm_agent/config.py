"""Environment-variable driven settings. Everything has a Chromebook-friendly default."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

ASSISTANT_NAME = os.environ.get("ASSISTANT_NAME", "Cortana")

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "llama3.2:3b")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")

MEMORY_DB_PATH = Path(os.environ.get("MEMORY_DB_PATH", REPO_ROOT / "data" / "memory.db"))
MEMORY_TOP_K = int(os.environ.get("MEMORY_TOP_K", "4"))

SEARCH_RESULTS = int(os.environ.get("SEARCH_RESULTS", "4"))
MAX_FETCH_CHARS = int(os.environ.get("MAX_FETCH_CHARS", "6000"))

REQUEST_TIMEOUT_SECONDS = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "20"))

# Conversation turns kept as context. Small local models have modest context
# windows, so this is capped rather than unbounded.
MAX_HISTORY_TURNS = int(os.environ.get("MAX_HISTORY_TURNS", "12"))

# SQLite is opened by both the CLI and the background daemon at the same time,
# so give writers room to wait instead of failing with "database is locked".
DB_BUSY_TIMEOUT_SECONDS = int(os.environ.get("DB_BUSY_TIMEOUT_SECONDS", "30"))

# Autonomous background research (see daemon.py)
CURIOSITY_INTERVAL_SECONDS = int(os.environ.get("CURIOSITY_INTERVAL_SECONDS", "1800"))
CURIOSITY_MAX_QUEUE_SIZE = int(os.environ.get("CURIOSITY_MAX_QUEUE_SIZE", "50"))
CURIOSITY_FOLLOW_UPS_PER_TOPIC = int(os.environ.get("CURIOSITY_FOLLOW_UPS_PER_TOPIC", "2"))

# How many times to retry a topic that failed for reasons of its own -- being
# offline, or Ollama not up yet. Matters most when the daemon starts at boot,
# before the network is ready.
CURIOSITY_MAX_ATTEMPTS = int(os.environ.get("CURIOSITY_MAX_ATTEMPTS", "5"))

# When the queue is empty there's nothing to pace, so poll more often than the
# research interval -- otherwise a topic added with /curious could sit unread
# for a full interval before the daemon notices it.
CURIOSITY_IDLE_POLL_SECONDS = int(os.environ.get("CURIOSITY_IDLE_POLL_SECONDS", "60"))

# Used only when the daemon starts with a completely empty queue and no topics
# were passed on the command line, so it always has somewhere to begin.
# Override with a semicolon-separated list, or set to "" to disable seeding.
_DEFAULT_SEEDS = (
    "how retrieval augmented generation works;"
    "running large language models locally on low-power hardware;"
    "ChromeOS Crostini Linux container tips"
)
SEED_TOPICS = [
    topic.strip()
    for topic in os.environ.get("SEED_TOPICS", _DEFAULT_SEEDS).split(";")
    if topic.strip()
]
