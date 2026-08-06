#!/usr/bin/env bash
# Build the downloadable Cortana bundle.
#
# Produces cortana.zip (ChromeOS Files can open .zip natively) and
# cortana.tar.gz (preserves the executable bit, for anyone extracting from a
# terminal). Both contain the same cortana/ folder.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-${REPO_DIR}/dist}"
STAGE="${OUT_DIR}/cortana"

rm -rf "${OUT_DIR}"
mkdir -p "${STAGE}"

# Everything needed to install and run. Deliberately excludes .venv, data/,
# logs and .git -- those are machine-specific and would only bloat it.
cp "${REPO_DIR}"/install.sh \
   "${REPO_DIR}"/install-launcher.sh \
   "${REPO_DIR}"/install-service.sh \
   "${REPO_DIR}"/launch-cortana.sh \
   "${REPO_DIR}"/setup.sh \
   "${REPO_DIR}"/requirements.txt \
   "${REPO_DIR}"/README.md \
   "${STAGE}/"

mkdir -p "${STAGE}/llm_agent" "${STAGE}/tools" "${STAGE}/assets" "${STAGE}/tests"
cp "${REPO_DIR}"/llm_agent/*.py "${STAGE}/llm_agent/"
cp "${REPO_DIR}"/tools/make_icon.py "${STAGE}/tools/"
cp "${REPO_DIR}"/assets/*.png "${STAGE}/assets/"
cp "${REPO_DIR}"/tests/*.py "${STAGE}/tests/"

cat > "${STAGE}/INSTALL.txt" <<'EOF'
Cortana - a local AI assistant for Chromebooks
==============================================

WHAT YOU NEED FIRST
-------------------
Linux turned on in ChromeOS:
  Settings -> Advanced -> Developers -> Linux development environment -> Turn on

Then move this whole "cortana" folder into the "Linux files" folder in the
ChromeOS Files app. (Files outside Linux files are not visible to the
installer.)


INSTALL (about 10-20 minutes, mostly downloading the model)
-----------------------------------------------------------
1. Open the Terminal app.
2. Run these two lines:

     cd ~/cortana
     bash install.sh

   Updating? If ChromeOS made a folder called "cortana (2)", just run it
   from there -- the installer finds your existing install, updates it,
   and keeps your chats. You do not need to rename or merge anything:

     cd ~/"cortana (2)"
     bash install.sh

   Use "bash install.sh", not "./install.sh" -- unzipping through the Files
   app removes the permission that lets a file run directly.

That is the whole install. It sets up Python, downloads Ollama and a model
sized to your Chromebook's memory, adds Cortana to your launcher, and starts
her background research.


USING IT
--------
Open the ChromeOS launcher and search for "Cortana".

If the icon does not appear right away, wait a minute -- ChromeOS takes a
moment to notice new Linux apps.


IF SOMETHING GOES WRONG
-----------------------
Nothing happens when clicking the icon:
    Look in cortana/launcher.log for the reason.

"model not found" or replies never arrive:
    ollama serve
  in a Terminal tab, then try again.

Check the background researcher:
    systemctl status cortana-daemon
    journalctl -u cortana-daemon -f


UNINSTALL
---------
    sudo systemctl disable --now cortana-daemon
    rm ~/.local/share/applications/cortana.desktop
    rm -rf ~/cortana

Full documentation is in README.md.
EOF

chmod +x "${STAGE}"/*.sh

# A list of everything this release ships. install.sh uses it to delete files
# left behind by an older version, so upgrading doesn't accumulate orphans.
( cd "${STAGE}" && find llm_agent tools assets tests -type f | sort ) \
    > "${STAGE}/MANIFEST.txt"

cd "${OUT_DIR}"
tar -czf cortana.tar.gz cortana
if command -v zip >/dev/null 2>&1; then
    zip -qr cortana.zip cortana
else
    echo "!! zip not installed; only the .tar.gz was built" >&2
fi

echo "Bundle built in ${OUT_DIR}:"
find "${OUT_DIR}" -maxdepth 1 -type f -printf '  %f  %s bytes\n' | sort
echo
echo "Contents:"
find cortana -type f | sort | sed 's/^/  /'
