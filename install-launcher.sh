#!/usr/bin/env bash
# Adds Cortana to the ChromeOS launcher and shelf, or on a Mac, to
# ~/Applications as Cortana.app.
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

if [ ! -f "${ICON_SRC}" ]; then
    echo "-- Rendering icon"
    mkdir -p "${REPO_DIR}/assets"
    python3 "${REPO_DIR}/tools/make_icon.py" "${ICON_SRC}"
fi

# Persist the chosen model for the launcher, since an app started from the
# desktop doesn't inherit the shell environment the installer ran in.
if [ -n "${CHAT_MODEL:-}" ]; then
    printf 'CHAT_MODEL=%s\n' "${CHAT_MODEL}" > "${REPO_DIR}/.env"
fi

if [ "$(uname -s)" = "Darwin" ]; then
    # A minimal .app bundle in ~/Applications: Launchpad, Spotlight and the
    # Dock all pick it up. It's a shell script, built on this machine, so
    # Gatekeeper has no download quarantine to object to.
    APP="${HOME}/Applications/Cortana.app"
    rm -rf "${APP}"
    mkdir -p "${APP}/Contents/MacOS" "${APP}/Contents/Resources"

    cat > "${APP}/Contents/MacOS/Cortana" <<EOF
#!/bin/bash
exec /bin/bash "${LAUNCHER}" "\$@"
EOF
    chmod 755 "${APP}/Contents/MacOS/Cortana"

    ICON_KEY=""
    if command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
        ICONSET="$(mktemp -d)/cortana.iconset"
        mkdir -p "${ICONSET}"
        for size in 16 32 128 256 512; do
            sips -z "${size}" "${size}" "${ICON_SRC}" \
                --out "${ICONSET}/icon_${size}x${size}.png" >/dev/null
            double=$((size * 2))
            if [ "${double}" -le 512 ]; then
                sips -z "${double}" "${double}" "${ICON_SRC}" \
                    --out "${ICONSET}/icon_${size}x${size}@2x.png" >/dev/null
            fi
        done
        if iconutil -c icns "${ICONSET}" -o "${APP}/Contents/Resources/cortana.icns"; then
            ICON_KEY="<key>CFBundleIconFile</key><string>cortana</string>"
        fi
        rm -rf "$(dirname "${ICONSET}")"
    fi

    cat > "${APP}/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Cortana</string>
    <key>CFBundleDisplayName</key><string>Cortana</string>
    <key>CFBundleIdentifier</key><string>local.cortana.app</string>
    <key>CFBundleExecutable</key><string>Cortana</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>NSHighResolutionCapable</key><true/>
    ${ICON_KEY}
</dict>
</plist>
EOF
    # Tell Launch Services the bundle changed, so Spotlight sees it now
    # rather than at the next scan.
    LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
    if [ -x "${LSREGISTER}" ]; then
        "${LSREGISTER}" -f "${APP}" >/dev/null 2>&1 || true
    fi

    echo "Added ${APP}. Open it from Launchpad or Spotlight (search \"Cortana\")."
    exit 0
fi

mkdir -p "${APP_DIR}" "${ICON_DIR}"
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

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${APP_DIR}" 2>/dev/null || true
fi

echo "Added to the launcher. Search for \"Cortana\" in the ChromeOS launcher."
echo "(It can take up to a minute to appear the first time.)"
