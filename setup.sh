#!/usr/bin/env bash
# Sets up the Chromebook LLM agent inside a Linux (Crostini) container.
set -euo pipefail

CHAT_MODEL="${CHAT_MODEL:-llama3.2:3b}"
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text}"

echo "== Chromebook LLM setup =="

if ! command -v ollama >/dev/null 2>&1; then
    echo "-- Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
else
    echo "-- Ollama already installed."
fi

if ! curl -s -o /dev/null "http://localhost:11434"; then
    echo "-- Ollama server doesn't seem to be running."
    echo "   Start it in another terminal tab with: ollama serve"
    echo "   Then re-run this script to pull the models."
    exit 1
fi

echo "-- Pulling chat model: ${CHAT_MODEL} (this can take a while on first run)"
ollama pull "${CHAT_MODEL}"

echo "-- Pulling embedding model: ${EMBED_MODEL}"
ollama pull "${EMBED_MODEL}"

echo "-- Setting up Python virtual environment"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

mkdir -p data

echo
echo "Setup complete. Next time, run:"
echo "  ollama serve            # in one terminal tab, if not already running"
echo "  source .venv/bin/activate && python -m llm_agent.cli   # in another"
