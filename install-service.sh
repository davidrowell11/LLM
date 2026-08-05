#!/usr/bin/env bash
# Installs Cortana's research daemon as a systemd service, so it runs
# automatically whenever the Linux container is up -- no terminal, no command.
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
    echo "!! ${REPO_DIR}/.venv not found. Run ./setup.sh first."
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
