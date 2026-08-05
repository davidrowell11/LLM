#!/usr/bin/env bash
# Sets up Cortana inside a Chromebook's Linux (Crostini) container.
#
# Ordering matters here: system packages and the Python environment are set up
# first and unconditionally, because they always work. Model pulls come last,
# since they're the only step that needs the Ollama server to be running --
# a failure there shouldn't leave you without a usable Python environment.
set -euo pipefail

CHAT_MODEL="${CHAT_MODEL:-}"
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text}"

echo "== Cortana setup =="

# --- 1. System packages -------------------------------------------------
# Debian (which Crostini uses) ships python3 without the venv module, and
# often without curl. Both are needed below, so install them up front.
MISSING_PKGS=()
python3 -c "import ensurepip" >/dev/null 2>&1 || MISSING_PKGS+=("python3-venv")
command -v pip3 >/dev/null 2>&1 || MISSING_PKGS+=("python3-pip")
command -v curl >/dev/null 2>&1 || MISSING_PKGS+=("curl")

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo "-- Installing system packages: ${MISSING_PKGS[*]}"
    sudo apt-get update
    sudo apt-get install -y "${MISSING_PKGS[@]}"
else
    echo "-- System packages already present."
fi

# --- 2. Pick a model that fits this Chromebook --------------------------
# Note: MemTotal reports what the Crostini VM actually got, which runs a few
# percent under the Chromebook's nominal RAM, and the division floors. So a
# "8GB" Chromebook reads as 7 and lands on the 2B model. That bias is
# deliberate -- an oversized model swap-thrashes the VM, which is a far worse
# experience than a smaller model that responds promptly.
TOTAL_RAM_KB=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
TOTAL_RAM_GB=$((TOTAL_RAM_KB / 1024 / 1024))
ARCH=$(uname -m)
echo "-- Detected ${TOTAL_RAM_GB}GB RAM, architecture ${ARCH}"

if [ -z "${CHAT_MODEL}" ]; then
    if [ "${TOTAL_RAM_GB}" -lt 4 ]; then
        CHAT_MODEL="llama3.2:1b"
        echo "   Low RAM -- defaulting to the 1B model."
    elif [ "${TOTAL_RAM_GB}" -lt 8 ]; then
        CHAT_MODEL="gemma2:2b"
        echo "   Defaulting to a 2B model to leave headroom."
    else
        CHAT_MODEL="llama3.2:3b"
        echo "   Plenty of RAM -- defaulting to the 3B model."
    fi
    echo "   Override any time with: export CHAT_MODEL=<model>"
fi

# --- 3. Python environment ----------------------------------------------
echo "-- Setting up Python virtual environment"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
mkdir -p data

# --- 4. Ollama ----------------------------------------------------------
if ! command -v ollama >/dev/null 2>&1; then
    echo "-- Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
else
    echo "-- Ollama already installed."
fi

# Crostini containers don't always run the Ollama systemd service, so start
# the server ourselves if nothing is listening yet.
if ! curl -s -o /dev/null --max-time 3 "http://localhost:11434"; then
    echo "-- Starting 'ollama serve' in the background (log: ollama.log)"
    nohup ollama serve >ollama.log 2>&1 &
    for _ in $(seq 1 20); do
        sleep 1
        if curl -s -o /dev/null --max-time 2 "http://localhost:11434"; then
            break
        fi
    done
fi

if ! curl -s -o /dev/null --max-time 3 "http://localhost:11434"; then
    echo
    echo "!! Ollama isn't responding on http://localhost:11434."
    echo "   Your Python environment is ready, but the models still need pulling."
    echo "   Start the server manually in another tab:  ollama serve"
    echo "   Then finish setup with:"
    echo "     ollama pull ${CHAT_MODEL} && ollama pull ${EMBED_MODEL}"
    exit 1
fi

echo "-- Pulling chat model: ${CHAT_MODEL} (this can take a while on first run)"
ollama pull "${CHAT_MODEL}"

echo "-- Pulling embedding model: ${EMBED_MODEL}"
ollama pull "${EMBED_MODEL}"

cat <<EOF

Setup complete.

  Chat with Cortana:
    source .venv/bin/activate
    CHAT_MODEL=${CHAT_MODEL} python -m llm_agent.cli

  Let her research on her own in the background:
    source .venv/bin/activate
    CHAT_MODEL=${CHAT_MODEL} python -m llm_agent.daemon

To make the model choice permanent, add this to ~/.bashrc:
    export CHAT_MODEL=${CHAT_MODEL}
EOF
