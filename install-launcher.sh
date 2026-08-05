#!/usr/bin/env bash
# Adds Cortana to the ChromeOS launcher and shelf.
#
# Crostini watches ~/.local/share/applications and mirrors any .desktop entry
# it finds into the ChromeOS launcher. Clicking that entry starts the Linux
# container if it isn't running -- which also starts the background research
# service -- and then opens the app.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${HOME}/.local/share/applications"
ICON_DIR="${HOME}/.local/share/icons/hicolor/512x512/apps"
ICON_SRC="${REPO_DIR}/assets/cortana.png"
LAUNCHER="${REPO_DIR}/launch-cortana.sh"

mkdir -p "${APP_DIR}" "${ICON_DIR}"

if [ ! -f "${ICON_SRC}" ]; then
    echo "-- Rendering icon"
    mkdir -p "${REPO_DIR}/assets"
    python3 "${REPO_DIR}/tools/make_icon.py" "${ICON_SRC}"
fi
install -m 644 "${ICON_SRC}" "${ICON_DIR}/cortana.png"

cat > "${APP_DIR}/cortana.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Cortana
GenericName=AI Assistant
Comment=Local AI assistant that researches and remembers
Exec=${LAUNCHER}
Path=${REPO_DIR}
Icon=${ICON_DIR}/cortana.png
Terminal=false
Categories=Utility;
Keywords=ai;assistant;chat;llm;
StartupNotify=true
StartupWMClass=Cortana
EOF
chmod 644 "${APP_DIR}/cortana.desktop"

# Persist the chosen model for the launcher, since a desktop entry doesn't
# inherit the shell environment the installer ran in.
if [ -n "${CHAT_MODEL:-}" ]; then
    printf 'CHAT_MODEL=%s\n' "${CHAT_MODEL}" > "${REPO_DIR}/.env"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${APP_DIR}" 2>/dev/null || true
fi

echo "Added to the launcher. Search for \"Cortana\" in the ChromeOS launcher."
echo "(It can take up to a minute to appear the first time.)"
