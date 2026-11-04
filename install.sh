#!/usr/bin/env bash
# One-shot installer for Cortana on a Chromebook (Linux/Crostini) or a Mac.
#
# Run this once and you get: system packages, the Python environment, Ollama
# and a right-sized model, the app in the launcher (ChromeOS) or Applications
# (Mac), and the background research daemon running on its own.
#
#   bash install.sh                 full install
#   bash install.sh --no-service    skip the background research daemon
#   bash install.sh --no-launcher   skip the launcher / Applications entry
#
# Use "bash install.sh" rather than "./install.sh": unzipping through the
# ChromeOS Files app strips the executable bit.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${REPO_DIR}"

WANT_SERVICE=1
WANT_LAUNCHER=1
for arg in "$@"; do
    case "$arg" in
        --no-service) WANT_SERVICE=0 ;;
        --no-launcher) WANT_LAUNCHER=0 ;;
        -h|--help) sed -n '2,14p' "$0" | sed -E 's/^# ?//'; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

IS_MAC=0
[ "$(uname -s)" = "Darwin" ] && IS_MAC=1
# Homebrew lives in /opt/homebrew on Apple Silicon and /usr/local on Intel,
# and a fresh Terminal may not have run its shellenv yet.
if [ "${IS_MAC}" -eq 1 ]; then
    for prefix in /opt/homebrew /usr/local; do
        if [ -x "${prefix}/bin/brew" ]; then PATH="${prefix}/bin:${PATH}"; fi
    done
    export PATH
fi

step() { printf '\n\033[36m==>\033[0m \033[1m%s\033[0m\n' "$1"; }

# Unpacking a .zip through the ChromeOS Files app drops the executable bit,
# so restore it rather than failing on "permission denied".
chmod +x "${REPO_DIR}"/*.sh 2>/dev/null || true

# Unpacking a second copy alongside an existing one gets named "cortana (2)"
# by the ChromeOS Files app. Running the installer from there would build a
# rival install next to the real one, so adopt the existing install instead
# of making the user merge folders by hand.
if [ ! -d "${REPO_DIR}/.venv" ] && [ ! -f "${REPO_DIR}/data/memory.db" ]; then
    for candidate in "$(dirname "${REPO_DIR}")"/*/; do
        candidate="${candidate%/}"
        [ "${candidate}" = "${REPO_DIR}" ] && continue
        if [ -d "${candidate}/llm_agent" ] &&
           { [ -d "${candidate}/.venv" ] || [ -f "${candidate}/data/memory.db" ]; }; then
            step "Found your existing install"
            echo "  at ${candidate}"
            echo "  Updating it in place rather than installing a second copy."
            cp -rf "${REPO_DIR}/." "${candidate}/"
            DUPLICATE_DIR="${REPO_DIR}"
            REPO_DIR="${candidate}"
            cd "${REPO_DIR}"
            break
        fi
    done
fi

if [ -d .venv ] || [ -f data/memory.db ]; then
    step "Updating an existing install"
    echo "Keeping your chats, memory and settings."

    # Delete files an older release shipped that this one no longer does.
    # Only the directories this project owns are touched -- data/, .venv/,
    # .env and logs are never considered.
    if [ -f MANIFEST.txt ]; then
        REMOVED=0
        while IFS= read -r found; do
            found="${found#./}"
            if ! grep -Fxq "${found}" MANIFEST.txt; then
                rm -f "${found}"
                echo "  removed obsolete ${found}"
                REMOVED=$((REMOVED + 1))
            fi
        done < <(find llm_agent tools assets tests -type f 2>/dev/null)
        [ "${REMOVED}" -eq 0 ] && echo "  nothing obsolete to remove."
    fi

    # Stale bytecode from deleted modules would otherwise still be importable.
    find . -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
fi

# Python 3.9+ with Tk 8.6 or newer. Apple's own /usr/bin/python3 ships Tk 8.5,
# which is deprecated and draws broken or blank windows on current macOS, so
# it's passed over in favour of Homebrew's or python.org's Python.
good_python() {
    "$1" -c 'import sys, tkinter
sys.exit(0 if sys.version_info >= (3, 9) and tkinter.TkVersion >= 8.6 else 1)' \
        >/dev/null 2>&1
}

find_python() {
    PYTHON=""
    for candidate in \
        "$(brew --prefix 2>/dev/null)/bin/python3" \
        /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
        /usr/local/bin/python3 \
        "$(command -v python3 2>/dev/null)"; do
        if [ -x "${candidate}" ] && good_python "${candidate}"; then
            PYTHON="${candidate}"
            return 0
        fi
    done
    return 1
}

mac_packages() {
    step "Checking Python"
    if ! find_python && command -v brew >/dev/null 2>&1; then
        echo "Installing Python with Tk through Homebrew..."
        brew install python python-tk
        find_python || true
    fi
    if [ -z "${PYTHON}" ]; then
        cat <<'MSG'
!! Cortana needs Python 3.9 or newer with Tk 8.6, and none was found.
   Apple's built-in python3 has an outdated Tk that can't draw the app.

   Either install Homebrew (https://brew.sh) and run this installer again,
   or install Python from https://www.python.org/downloads/macos/ (its
   installer includes Tk) and run this installer again.
MSG
        exit 1
    fi
    echo "Using ${PYTHON} ($("${PYTHON}" -c 'import sys, tkinter; print(sys.version.split()[0], "with Tk", tkinter.TkVersion)'))"
}

if [ "${IS_MAC}" -eq 1 ]; then
    mac_packages
else
    PYTHON=python3
    step "Checking system packages"
    # Debian ships python3 without venv, and without the Tk bindings the app has
    # no window to draw into. Installing Tk before creating the venv means the
    # environment picks it up straight away.
    MISSING=()
    python3 -c "import ensurepip" >/dev/null 2>&1 || MISSING+=("python3-venv")
    python3 -c "import tkinter"   >/dev/null 2>&1 || MISSING+=("python3-tk")
    command -v pip3 >/dev/null 2>&1 || MISSING+=("python3-pip")
    command -v curl >/dev/null 2>&1 || MISSING+=("curl")
    # A bare Crostini container only ships DejaVu; Roboto is what the rest of
    # ChromeOS uses and makes the app look like it belongs on the device.
    fc-list 2>/dev/null | grep -qi roboto || MISSING+=("fonts-roboto")

    if [ ${#MISSING[@]} -gt 0 ]; then
        echo "Installing: ${MISSING[*]}"
        sudo apt-get update
        sudo apt-get install -y "${MISSING[@]}"
    else
        echo "All present."
    fi
fi

if [ "${IS_MAC}" -eq 1 ]; then
    step "Sizing the model to this Mac"
    TOTAL_RAM_GB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
else
    step "Sizing the model to this Chromebook"
    TOTAL_RAM_GB=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 / 1024 ))
fi
echo "Detected ${TOTAL_RAM_GB}GB RAM ($(uname -m))"
if [ -z "${CHAT_MODEL:-}" ]; then
    if   [ "${TOTAL_RAM_GB}" -lt 4 ]; then CHAT_MODEL="llama3.2:1b"
    elif [ "${TOTAL_RAM_GB}" -lt 8 ]; then CHAT_MODEL="gemma2:2b"
    else                                   CHAT_MODEL="llama3.2:3b"
    fi
fi
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text}"
echo "Chat model: ${CHAT_MODEL}   (override with CHAT_MODEL=... bash install.sh)"

step "Building the Python environment"
# A venv keeps the interpreter it was made with, so one built from Apple's
# python3 would keep its broken Tk forever. Rebuild it (chats and memory live
# in data/, not here).
if [ "${IS_MAC}" -eq 1 ] && [ -x .venv/bin/python ] && ! good_python .venv/bin/python; then
    echo "Rebuilding .venv on ${PYTHON}"
    rm -rf .venv
fi
"${PYTHON}" -m venv .venv
./.venv/bin/pip install --upgrade -q pip
./.venv/bin/pip install -q -r requirements.txt
mkdir -p data
./.venv/bin/python -c "import tkinter" 2>/dev/null \
    && echo "Window toolkit available." \
    || echo "!! tkinter missing - the app will run in the terminal only."

ollama_up() { curl -s -o /dev/null --max-time 3 "http://localhost:11434"; }

wait_for_ollama() {
    for _ in $(seq 1 20); do
        sleep 1
        ollama_up && return 0
    done
    return 1
}

mac_ollama() {
    step "Installing Ollama"
    # Ollama's install.sh is Linux-only. On a Mac it's either the desktop app
    # (which also puts the ollama command on PATH) or the Homebrew formula.
    if [ -d /Applications/Ollama.app ] || command -v ollama >/dev/null 2>&1; then
        echo "Already installed."
    elif command -v brew >/dev/null 2>&1; then
        brew install ollama
    else
        echo "!! Ollama isn't installed and Homebrew isn't available."
        echo "   Download it from https://ollama.com/download, open it once,"
        echo "   then run this installer again to fetch the models."
        return 0
    fi

    ollama_up && return 0
    # Both of these keep models in ~/.ollama and start again at login, so
    # what's pulled now is still there after a reboot.
    if [ -d /Applications/Ollama.app ]; then
        echo "Starting the Ollama app..."
        open -g -a Ollama || true
    elif command -v brew >/dev/null 2>&1 && brew list ollama >/dev/null 2>&1; then
        echo "Starting Ollama as a login service..."
        brew services start ollama >/dev/null 2>&1 || true
    fi
    wait_for_ollama && return 0
    if command -v ollama >/dev/null 2>&1; then
        echo "Starting the model server directly..."
        nohup ollama serve >ollama.log 2>&1 &
        wait_for_ollama || true
    fi
}

if [ "${IS_MAC}" -eq 1 ]; then
    mac_ollama
else
    step "Installing Ollama"
    if ! command -v ollama >/dev/null 2>&1; then
        curl -fsSL https://ollama.com/install.sh | sh
    else
        echo "Already installed."
    fi

    if ! ollama_up; then
        # Prefer Ollama's own service if its installer created one. This matters
        # beyond convenience: that service runs as the 'ollama' user and keeps
        # models under /usr/share/ollama, while a server started by hand here
        # would store them under $HOME. Pulling into the wrong one looks fine
        # today and then fails at the next reboot with "model not found", because
        # the service that starts at boot can't see them.
        if command -v systemctl >/dev/null 2>&1 && systemctl cat ollama.service >/dev/null 2>&1; then
            echo "Starting the packaged ollama service..."
            sudo systemctl enable --now ollama.service >/dev/null 2>&1 || true
            for _ in $(seq 1 20); do
                sleep 1
                ollama_up && break
            done
        fi
    fi

    if ! ollama_up; then
        echo "Starting the model server directly..."
        nohup ollama serve >ollama.log 2>&1 &
        for _ in $(seq 1 20); do
            sleep 1
            ollama_up && break
        done
    fi
fi

if ollama_up; then
    step "Downloading models (this is the slow part)"
    # Pulls go to whichever server is listening, which is now the same one
    # that will be running after a reboot.
    ollama pull "${CHAT_MODEL}"
    ollama pull "${EMBED_MODEL}"
else
    echo "!! Ollama isn't responding; skipping model download."
    echo "   Later, run: ollama serve & ollama pull ${CHAT_MODEL}"
fi

if [ "${WANT_LAUNCHER}" -eq 1 ]; then
    if [ "${IS_MAC}" -eq 1 ]; then
        step "Adding Cortana to Applications"
    else
        step "Adding Cortana to the ChromeOS launcher"
    fi
    CHAT_MODEL="${CHAT_MODEL}" bash ./install-launcher.sh
fi

if [ "${WANT_SERVICE}" -eq 1 ]; then
    step "Setting up background research"
    CHAT_MODEL="${CHAT_MODEL}" bash ./install-service.sh || {
        echo "!! Couldn't install the service; background research is off."
        echo "   Run it by hand with: ./.venv/bin/python -m llm_agent.daemon"
    }
    # An already-running daemon holds the old code in memory, so an upgrade
    # only takes effect once it restarts.
    # (On a Mac, install-service.sh reloads the LaunchAgent itself.)
    if [ "${IS_MAC}" -eq 0 ] && command -v systemctl >/dev/null 2>&1 &&
       systemctl cat cortana-daemon.service >/dev/null 2>&1; then
        sudo systemctl restart cortana-daemon.service >/dev/null 2>&1 \
            && echo "Restarted the research daemon on the new version."
    fi
fi

if [ -n "${DUPLICATE_DIR:-}" ]; then
    cat <<EOF

$(printf '\033[1;33m')Note:$(printf '\033[0m') your install lives at ${REPO_DIR}
The folder you ran this from is now a leftover copy. Remove it with:
    rm -rf "${DUPLICATE_DIR}"
EOF
fi

if [ "${IS_MAC}" -eq 1 ]; then
    OPEN_HINT='Open Cortana from Launchpad or Spotlight (search for "Cortana"), or run:'
    WATCH_HINT="tail -f ${REPO_DIR}/daemon.log"
else
    OPEN_HINT='Open Cortana from the ChromeOS launcher (search for "Cortana"), or run:'
    WATCH_HINT="journalctl -u cortana-daemon -f"
fi

cat <<EOF

$(printf '\033[1;36m')Done.$(printf '\033[0m')

  ${OPEN_HINT}
    ./.venv/bin/python -m llm_agent.gui      the app
    ./.venv/bin/python -m llm_agent.cli      the terminal version

  She researches on her own in the background. To watch:
    ${WATCH_HINT}
EOF
