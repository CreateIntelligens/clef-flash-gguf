#!/bin/bash
set -e

# ==============================================================================
# Clef-Flash GGUF 模型與多模態投影矩陣下載腳本
# 來源: https://huggingface.co/bartowski/Cloudflare_clef-flash-GGUF
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS_DIR="${1:-$(dirname "$SCRIPT_DIR")/models}"
mkdir -p "$MODELS_DIR"

BASE_URL="https://huggingface.co/bartowski/Cloudflare_clef-flash-GGUF/resolve/main"

echo "=========================================================="
echo "目標下載目錄: $MODELS_DIR"
echo "=========================================================="

# 1. 下載主模型權重: Cloudflare_clef-flash-Q4_K_M.gguf (~5.7 GB)
MAIN_MODEL="$MODELS_DIR/Cloudflare_clef-flash-Q4_K_M.gguf"
if [ ! -f "$MAIN_MODEL" ]; then
    echo "[1/2] 下載 Q4_K_M 主模型 (約 5.7 GB)..."
    curl -L -C - "$BASE_URL/Cloudflare_clef-flash-Q4_K_M.gguf?download=true" -o "$MAIN_MODEL"
else
    echo "[1/2] $MAIN_MODEL 已存在，略過下載。"
fi

# 2. 下載多模態投影矩陣: mmproj-Cloudflare_clef-flash-f16.gguf (~876 MB)
MMPROJ_MODEL="$MODELS_DIR/mmproj-Cloudflare_clef-flash-f16.gguf"
if [ ! -f "$MMPROJ_MODEL" ]; then
    echo "[2/2] 下載 mmproj 多模態投影矩陣 (約 876 MB)..."
    curl -L -C - "$BASE_URL/mmproj-Cloudflare_clef-flash-f16.gguf?download=true" -o "$MMPROJ_MODEL"
else
    echo "[2/2] $MMPROJ_MODEL 已存在，略過下載。"
fi

echo "=========================================================="
echo "✅ 模型下載完畢！檔案清單："
ls -lh "$MODELS_DIR"
echo "=========================================================="
