#!/usr/bin/env bash
# Entry point used by the ChromeOS launcher entry.
#
# A .desktop entry starts with a bare environment and no terminal attached,
# so anything that goes wrong here is invisible by default -- clicking the
# icon would simply do nothing. Everything below therefore reports failures
# through a dialog and a log file rather than stderr.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Not using `set -e` here, since the app's exit status has to be inspected
# below rather than aborting the script -- so guard cd explicitly.
cd "${REPO_DIR}" || exit 1
LOG="${REPO_DIR}/launcher.log"

report() {
    printf '[%s] %s\n' "$(date -Is)" "$1" >>"${LOG}"
    # Whichever dialog tool this container happens to have.
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --no-wrap --title="Cortana" --text="$1" 2>/dev/null && return
    fi
    if command -v xmessage >/dev/null 2>&1; then
        xmessage -center "Cortana: $1" 2>/dev/null && return
    fi
    if command -v notify-send >/dev/null 2>&1; then
        notify-send "Cortana" "$1" 2>/dev/null && return
    fi
    echo "Cortana: $1" >&2
}

if [ ! -x "./.venv/bin/python" ]; then
    report "Cortana isn't installed yet.

Open the Terminal app and run:
  cd ${REPO_DIR} && ./install.sh"
    exit 1
fi

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
    if command -v systemctl >/dev/null 2>&1 && systemctl cat ollama.service >/dev/null 2>&1; then
        sudo -n systemctl start ollama.service >/dev/null 2>&1 || true
    fi
    if ! curl -s -o /dev/null --max-time 2 "http://localhost:11434" 2>/dev/null; then
        if command -v ollama >/dev/null 2>&1; then
            nohup ollama serve >>ollama.log 2>&1 &
        fi
    fi
    for _ in $(seq 1 10); do
        sleep 1
        curl -s -o /dev/null --max-time 1 "http://localhost:11434" 2>/dev/null && break
    done
fi

# Not exec'd, so a crash can still be reported instead of vanishing.
./.venv/bin/python -m llm_agent.gui "$@" 2>>"${LOG}"
status=$?

if [ "${status}" -ne 0 ]; then
    report "Cortana couldn't start (exit ${status}).

Last lines of ${LOG}:
$(tail -n 5 "${LOG}" 2>/dev/null)"
fi
exit "${status}"
