#!/bin/bash
set -e

# 切換至專案根目錄
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# 啟動虛擬環境並執行 API 門面
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

echo "Starting Clef-Flash API Server..."
exec python3 api_server.py
