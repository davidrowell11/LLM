#!/usr/bin/env bash
# Sets up Cortana's Python environment, Ollama and models -- without the
# launcher entry or the background service.
#
# Kept for anyone used to running it; install.sh does the same work on both
# Chromebooks and Macs, so this just hands over to it. Use install.sh for a
# full install.
set -euo pipefail

exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/install.sh" \
    --no-launcher --no-service "$@"
