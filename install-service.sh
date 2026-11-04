#!/usr/bin/env bash
# Installs Cortana's research daemon as a systemd service, so it runs
# automatically whenever the Linux container is up -- no terminal, no command.
# On a Mac it installs a LaunchAgent instead, which runs while you're logged in.
#
# Scope, honestly: the Crostini container only runs while ChromeOS's Linux is
# running. ChromeOS starts it when you open any Linux app and stops it when
# Linux shuts down or the Chromebook powers off. So this means "starts by
# itself, without you typing anything" -- not "runs while the Chromebook is
# off". Nothing installed inside the container can do the latter.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="$(id -un)"
UNIT_DIR="/etc/systemd/system"
CORTANA_UNIT="${UNIT_DIR}/cortana-daemon.service"
OLLAMA_UNIT="${UNIT_DIR}/ollama.service"

echo "== Installing Cortana research daemon as a service =="

if [ "$(uname -s)" = "Darwin" ]; then
    # macOS has no systemd; a per-user LaunchAgent is the equivalent. It runs
    # while you're logged in, starts again at every login, and is restarted
    # if it exits -- no sudo needed.
    if [ ! -x "${REPO_DIR}/.venv/bin/python" ]; then
        echo "!! ${REPO_DIR}/.venv not found. Run: bash install.sh"
        exit 1
    fi

    # Background processes don't get the privacy permission Terminal has, so
    # a daemon reading its code from these folders fails with "Operation not
    # permitted" and never says why anywhere you'd look.
    case "${REPO_DIR}/" in
        "${HOME}/Desktop/"*|"${HOME}/Documents/"*|"${HOME}/Downloads/"*|"${HOME}/Library/Mobile Documents/"*)
            echo "!! Cortana is in a folder macOS protects (${REPO_DIR})."
            echo "   Background research can't read it from there. Move the folder"
            echo "   to your home folder (for example ~/cortana) and run install.sh again."
            exit 1
            ;;
    esac

    LABEL="local.cortana.daemon"
    AGENT_DIR="${HOME}/Library/LaunchAgents"
    PLIST="${AGENT_DIR}/${LABEL}.plist"
    mkdir -p "${AGENT_DIR}"

    # XML-escape the few characters a folder name could contain that would
    # break the plist.
    xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

    cat > "${PLIST}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(xml "${REPO_DIR}/.venv/bin/python")</string>
        <string>-m</string>
        <string>llm_agent.daemon</string>
    </array>
    <key>WorkingDirectory</key><string>$(xml "${REPO_DIR}")</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>CHAT_MODEL</key><string>$(xml "${CHAT_MODEL:-llama3.2:3b}")</string>
        <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>PYTHONUNBUFFERED</key><string>1</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <!-- After a crash, wait rather than spinning (matches systemd's 30s). -->
    <key>ThrottleInterval</key><integer>30</integer>
    <key>ProcessType</key><string>Background</string>
    <key>StandardOutPath</key><string>$(xml "${REPO_DIR}/daemon.log")</string>
    <key>StandardErrorPath</key><string>$(xml "${REPO_DIR}/daemon.log")</string>
</dict>
</plist>
EOF

    # bootout first so a re-install (an upgrade) restarts it on the new code.
    DOMAIN="gui/$(id -u)"
    launchctl bootout "${DOMAIN}/${LABEL}" >/dev/null 2>&1 || true
    launchctl bootstrap "${DOMAIN}" "${PLIST}"

    # The daemon needs a model server that also comes back after a reboot.
    if [ -d /Applications/Ollama.app ]; then
        OLLAMA_NOTE="The Ollama app starts the model server; keep it in your login items."
    elif command -v brew >/dev/null 2>&1 && brew list ollama >/dev/null 2>&1; then
        brew services start ollama >/dev/null 2>&1 || true
        OLLAMA_NOTE="Ollama runs as a Homebrew service and starts at login."
    else
        OLLAMA_NOTE="Make sure Ollama is running (ollama serve) -- research waits for it."
    fi

    cat <<EOF

Installed and started. It now runs whenever you're logged in.
${OLLAMA_NOTE}

  Check it's running :  launchctl print ${DOMAIN}/${LABEL} | grep state
  Watch what it learns: tail -f "${REPO_DIR}/daemon.log"
  Stop until next login: launchctl bootout ${DOMAIN}/${LABEL}
  Disable permanently :  launchctl bootout ${DOMAIN}/${LABEL}; rm "${PLIST}"
EOF
    exit 0
fi

if ! command -v systemctl >/dev/null 2>&1 || ! systemctl list-units >/dev/null 2>&1; then
    cat <<'EOF'
!! systemd isn't available in this container, so a service can't be installed.

   Fall back to cron, which runs the daemon at container start. Add this with
   `crontab -e` (installing cron first if needed: sudo apt-get install -y cron):

     @reboot cd /path/to/cortana && ./.venv/bin/python -m llm_agent.daemon >> daemon.log 2>&1
EOF
    exit 1
fi

if [ ! -x "${REPO_DIR}/.venv/bin/python" ]; then
    echo "!! ${REPO_DIR}/.venv not found. Run: bash install.sh"
    exit 1
fi

# Ollama's own installer only sets up a unit on some systems, so make sure one
# exists -- the daemon is useless without a model server, and at boot it would
# otherwise spend its retries waiting for something that never starts.
if ! systemctl list-unit-files ollama.service >/dev/null 2>&1 ||
   ! systemctl cat ollama.service >/dev/null 2>&1; then
    echo "-- Creating ollama.service"
    sudo tee "${OLLAMA_UNIT}" >/dev/null <<EOF
[Unit]
Description=Ollama model server
After=network-online.target

[Service]
ExecStart=$(command -v ollama) serve
User=${SERVICE_USER}
Restart=always
RestartSec=5
Environment=HOME=${HOME}

[Install]
WantedBy=multi-user.target
EOF
else
    echo "-- ollama.service already present."
fi

echo "-- Creating cortana-daemon.service"
# CHAT_MODEL is captured now so the service uses the same model setup chose.
sudo tee "${CORTANA_UNIT}" >/dev/null <<EOF
[Unit]
Description=Cortana autonomous research daemon
# Wants= rather than Requires= so Ollama being slow or down never stops the
# daemon from starting; it retries topics instead of losing them.
Wants=network-online.target ollama.service
After=network-online.target ollama.service

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${REPO_DIR}
Environment=HOME=${HOME}
Environment=CHAT_MODEL=${CHAT_MODEL:-llama3.2:3b}
ExecStart=${REPO_DIR}/.venv/bin/python -m llm_agent.daemon
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now ollama.service 2>/dev/null || true
sudo systemctl enable --now cortana-daemon.service

echo
echo "Installed and started. It will now start on its own with the container."
cat <<EOF

  Check it's running :  systemctl status cortana-daemon
  Watch what it learns: journalctl -u cortana-daemon -f
  Stop for now        :  sudo systemctl stop cortana-daemon
  Disable permanently :  sudo systemctl disable --now cortana-daemon
EOF
