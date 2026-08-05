#!/usr/bin/env bash
# One-shot installer for Cortana on a Chromebook (Linux/Crostini).
#
# Run this once and you get: system packages, the Python environment, Ollama
# and a right-sized model, the app in the ChromeOS launcher, and the
# background research daemon running on its own.
#
#   ./install.sh                 full install
#   ./install.sh --no-service    skip the background research daemon
#   ./install.sh --no-launcher   skip the ChromeOS launcher entry
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${REPO_DIR}"

WANT_SERVICE=1
WANT_LAUNCHER=1
for arg in "$@"; do
    case "$arg" in
        --no-service) WANT_SERVICE=0 ;;
        --no-launcher) WANT_LAUNCHER=0 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

step() { printf '\n\033[36m==>\033[0m \033[1m%s\033[0m\n' "$1"; }

step "Checking system packages"
# Debian ships python3 without venv, and without the Tk bindings the app has
# no window to draw into. Installing Tk before creating the venv means the
# environment picks it up straight away.
MISSING=()
python3 -c "import ensurepip" >/dev/null 2>&1 || MISSING+=("python3-venv")
python3 -c "import tkinter"   >/dev/null 2>&1 || MISSING+=("python3-tk")
command -v pip3 >/dev/null 2>&1 || MISSING+=("python3-pip")
command -v curl >/dev/null 2>&1 || MISSING+=("curl")

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "Installing: ${MISSING[*]}"
    sudo apt-get update
    sudo apt-get install -y "${MISSING[@]}"
else
    echo "All present."
fi

step "Sizing the model to this Chromebook"
TOTAL_RAM_GB=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 / 1024 ))
echo "Detected ${TOTAL_RAM_GB}GB RAM ($(uname -m))"
if [ -z "${CHAT_MODEL:-}" ]; then
    if   [ "${TOTAL_RAM_GB}" -lt 4 ]; then CHAT_MODEL="llama3.2:1b"
    elif [ "${TOTAL_RAM_GB}" -lt 8 ]; then CHAT_MODEL="gemma2:2b"
    else                                   CHAT_MODEL="llama3.2:3b"
    fi
fi
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text}"
echo "Chat model: ${CHAT_MODEL}   (override with CHAT_MODEL=... ./install.sh)"

step "Building the Python environment"
python3 -m venv .venv
./.venv/bin/pip install --upgrade -q pip
./.venv/bin/pip install -q -r requirements.txt
mkdir -p data
./.venv/bin/python -c "import tkinter" 2>/dev/null \
    && echo "Window toolkit available." \
    || echo "!! tkinter missing - the app will run in the terminal only."

step "Installing Ollama"
if ! command -v ollama >/dev/null 2>&1; then
    curl -fsSL https://ollama.com/install.sh | sh
else
    echo "Already installed."
fi

if ! curl -s -o /dev/null --max-time 3 "http://localhost:11434"; then
    echo "Starting the model server..."
    nohup ollama serve >ollama.log 2>&1 &
    for _ in $(seq 1 20); do
        sleep 1
        curl -s -o /dev/null --max-time 2 "http://localhost:11434" && break
    done
fi

if curl -s -o /dev/null --max-time 3 "http://localhost:11434"; then
    step "Downloading models (this is the slow part)"
    ollama pull "${CHAT_MODEL}"
    ollama pull "${EMBED_MODEL}"
else
    echo "!! Ollama isn't responding; skipping model download."
    echo "   Later, run: ollama serve & ollama pull ${CHAT_MODEL}"
fi

if [ "${WANT_LAUNCHER}" -eq 1 ]; then
    step "Adding Cortana to the ChromeOS launcher"
    CHAT_MODEL="${CHAT_MODEL}" ./install-launcher.sh
fi

if [ "${WANT_SERVICE}" -eq 1 ]; then
    step "Setting up background research"
    CHAT_MODEL="${CHAT_MODEL}" ./install-service.sh || {
        echo "!! Couldn't install the service; background research is off."
        echo "   Run it by hand with: ./.venv/bin/python -m llm_agent.daemon"
    }
fi

cat <<EOF

$(printf '\033[1;36m')Done.$(printf '\033[0m')

  Open Cortana from the ChromeOS launcher (search for "Cortana"), or run:
    ./.venv/bin/python -m llm_agent.gui      the app
    ./.venv/bin/python -m llm_agent.cli      the terminal version

  She researches on her own in the background. To watch:
    journalctl -u cortana-daemon -f
EOF
