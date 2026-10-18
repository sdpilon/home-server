#!/usr/bin/env bash
# Injects the account/profile directory so that `init`, `config`, `serve`, ...
# all operate on the same volume-mounted state.
set -euo pipefail

exec python -m bridge \
    --config-dir "${DC_CONFIG_DIR:-/data/dc}" \
    --logging "${BRIDGE_LOG:-info}" \
    "$@"
