#!/usr/bin/env bash
# ==============================================================================
# Cloudflare Clef-Flash GGUF 一鍵自動安裝與環境部署指令檔
# ==============================================================================
# 使用方式：
#   ./install.sh              # 建立 Python 虛擬環境並自動從 Hugging Face 下載模型
#   ./install.sh --skip-venv  # 跳過虛擬環境建立，僅下載模型
#   ./install.sh --check-only # 僅檢查模型檔案完整性
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

C_RESET="\033[0m"
C_BOLD="\033[1m"
C_GREEN="\033[32m"
C_YELLOW="\033[33m"
C_CYAN="\033[36m"
C_RED="\033[31m"

echo "=================================================================="
echo -e "${C_BOLD}${C_GREEN}🚀 Cloudflare Clef-Flash GGUF 一鍵安裝部署程序${C_RESET}"
echo "=================================================================="

# 1. 檢查 Python3
if ! command -v python3 &>/dev/null; then
    echo -e "${C_RED}❌ 找不到 python3，請先安裝 Python 3.10 以上版本。${C_RESET}"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo -e "${C_CYAN}ℹ️  Python 版本: ${PYTHON_VERSION}${C_RESET}"

# 2. 處理虛擬環境與相依套件 (若未指定 --skip-venv 且未指定 --check-only)
SKIP_VENV=0
CHECK_ONLY=0

for arg in "$@"; do
    if [ "$arg" == "--skip-venv" ]; then
        SKIP_VENV=1
    fi
    if [ "$arg" == "--check-only" ]; then
        CHECK_ONLY=1
    fi
done

if [ "$SKIP_VENV" -eq 0 ] && [ "$CHECK_ONLY" -eq 0 ]; then
    if [ ! -d ".venv" ]; then
        echo -e "\n${C_BOLD}[1/2] 正在建立 Python 虛擬環境 (.venv)...${C_RESET}"
        python3 -m venv .venv
    else
        echo -e "\n${C_BOLD}[1/2] 偵測到現有 Python 虛擬環境 (.venv)${C_RESET}"
    fi

    # 啟用虛擬環境
    source .venv/bin/activate
    echo -e "      安裝/更新必要相依套件 (fastapi, httpx, pydantic, uvicorn, huggingface_hub)..."
    pip install --upgrade pip --quiet
    pip install -r requirements.txt --quiet
    # 額外安裝 huggingface_hub 以獲取最穩定的多執行緒斷點續傳速度
    pip install huggingface_hub --quiet
    echo -e "      ${C_GREEN}✅ 虛擬環境相依套件準備就緒！${C_RESET}"
    PYTHON_EXEC=".venv/bin/python3"
else
    PYTHON_EXEC="python3"
fi

# 3. 呼叫 install.py 執行磁碟檢查、模型下載與校驗
echo -e "\n${C_BOLD}[2/2] 執行 Hugging Face 模型下載與校驗程序...${C_RESET}"
$PYTHON_EXEC install.py "$@"

exit 0
