#!/usr/bin/env bash
# Entry point used by the ChromeOS launcher entry.
#
# A .desktop entry starts with a bare environment, so anything the app needs
# has to be set up here rather than assumed from a shell profile.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${REPO_DIR}"

# Model choice recorded at install time.
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

# The launcher can start before the model server is up; give it a moment
# rather than opening to an error.
if ! curl -s -o /dev/null --max-time 2 "http://localhost:11434" 2>/dev/null; then
    if command -v ollama >/dev/null 2>&1; then
        nohup ollama serve >>ollama.log 2>&1 &
        for _ in $(seq 1 10); do
            sleep 1
            curl -s -o /dev/null --max-time 1 "http://localhost:11434" 2>/dev/null && break
        done
    fi
fi

exec ./.venv/bin/python -m llm_agent.gui "$@"
