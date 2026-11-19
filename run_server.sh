#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)"
source "$SCRIPT_DIR/.venv/bin/activate"
exec "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/api_server.py"
