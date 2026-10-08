#!/usr/bin/env python3
"""
==============================================================================
Clef-Flash GGUF 模型與環境全自動安裝工具 (Automated Installer)
==============================================================================
功能：
1. 自動檢查磁碟剩餘空間 (需至少 8 GB)。
2. 自動從 Hugging Face (bartowski/Cloudflare_clef-flash-GGUF) 下載：
   - Q4_K_M 主模型 (~5.7 GB): Cloudflare_clef-flash-Q4_K_M.gguf
   - F16 多模態投影矩陣 (~876 MB): mmproj-Cloudflare_clef-flash-f16.gguf
3. 支援斷點續傳 (HTTP Range Header)、進度條與傳輸速率計算。
4. 優先使用 huggingface_hub（若已安裝），亦支援純 Python 標準庫零依賴下載。
5. 校驗檔案大小與完整性。
==============================================================================
"""

import os
import sys
import time
import shutil
import argparse
import urllib.request
import urllib.error
from pathlib import Path

REPO_ID = "bartowski/Cloudflare_clef-flash-GGUF"
BASE_URL = f"https://huggingface.co/{REPO_ID}/resolve/main"

MODELS_CONFIG = [
    {
        "filename": "Cloudflare_clef-flash-Q4_K_M.gguf",
        "description": "Clef-Flash Q4_K_M 4-bit 量化主模型",
        "expected_size": 6040541344,  # ~5.62 GiB (6.04 GB)
        "required": True,
    },
    {
        "filename": "mmproj-Cloudflare_clef-flash-f16.gguf",
        "description": "Clef-Flash F16 多模態投影矩陣 (Multimodal Projector)",
        "expected_size": 918165984,   # ~875.6 MiB (918 MB)
        "required": True,
    }
]

# ANSI 顏色終端格式
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_CYAN = "\033[36m"
C_RED = "\033[31m"
C_GRAY = "\033[90m"


def format_bytes(num_bytes: int) -> str:
    """轉換位元組為易讀格式 (B, KB, MB, GB)。"""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:3.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


def check_disk_space(target_dir: Path, min_required_bytes: int = 8 * 1024 * 1024 * 1024):
    """檢查目標路徑所在磁碟空間。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    total, used, free = shutil.disk_usage(target_dir)
    print(f"{C_CYAN}ℹ️  磁碟空間檢查{C_RESET}: 目標目錄 {target_dir}")
    print(f"   剩餘可用空間: {C_BOLD}{format_bytes(free)}{C_RESET} (需要約 {format_bytes(min_required_bytes)})")

    if free < min_required_bytes:
        print(f"\n{C_RED}❌ 錯誤: 磁碟剩餘空間不足！{C_RESET}")
        print(f"   需至少 {format_bytes(min_required_bytes)}，但僅有 {format_bytes(free)}。")
        print(f"   請清理磁碟空間後再重試。")
        sys.exit(1)


def download_with_hf_hub(repo_id: str, filename: str, target_path: Path, token: str = None) -> bool:
    """嘗試使用 huggingface_hub 下載（若已安裝）。"""
    try:
        from huggingface_hub import hf_hub_download
        print(f"{C_CYAN}🚀 使用 huggingface_hub 高速下載引擎...{C_RESET}")
        cached_file = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=target_path.parent,
            local_dir_use_symlinks=False,
            token=token
        )
        return True
    except ImportError:
        return False
    except Exception as e:
        print(f"{C_YELLOW}⚠️ huggingface_hub 下載遇到狀況 ({e})，切換為內建斷點續傳引擎...{C_RESET}")
        return False


def download_with_urllib(url: str, target_path: Path, expected_size: int, token: str = None):
    """使用純 Python 內建 urllib 進行斷點續傳下載與進度條顯示。"""
    temp_path = target_path.with_suffix(target_path.suffix + ".part")
    existing_bytes = 0

    if temp_path.exists():
        existing_bytes = temp_path.stat().st_size
        if existing_bytes > expected_size:
            # 檔案異常，重置
            existing_bytes = 0
            temp_path.unlink()

    headers = {
        "User-Agent": "Clef-Flash-GGUF-Installer/1.0",
        "Accept-Encoding": "identity",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    if existing_bytes > 0:
        headers["Range"] = f"bytes={existing_bytes}-"
        print(f"{C_YELLOW}⚡ 偵測到未完成下載，自 {format_bytes(existing_bytes)} 處執行斷點續傳...{C_RESET}")

    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            total_size = expected_size

            # 若伺服器回傳 206 Partial Content
            mode = "ab" if existing_bytes > 0 and status == 206 else "wb"
            if mode == "wb":
                existing_bytes = 0

            downloaded = existing_bytes
            start_time = time.time()
            last_print_time = 0
            block_size = 1024 * 1024  # 1 MB 緩衝

            with open(temp_path, mode) as f:
                while True:
                    chunk = resp.read(block_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    now = time.time()
                    if now - last_print_time > 0.3:
                        last_print_time = now
                        elapsed = now - start_time
                        speed = (downloaded - existing_bytes) / elapsed if elapsed > 0 else 0
                        percent = (downloaded / total_size * 100) if total_size > 0 else 0
                        eta_seconds = (total_size - downloaded) / speed if speed > 0 else 0
                        eta_str = time.strftime("%H:%M:%S", time.gmtime(eta_seconds)) if eta_seconds < 86400 else ">1d"

                        # 進度條渲染
                        bar_len = 30
                        filled = int(bar_len * downloaded / total_size) if total_size > 0 else 0
                        bar = "█" * filled + "░" * (bar_len - filled)

                        print(
                            f"\r{C_GREEN}[{bar}]{C_RESET} {percent:5.1f}% "
                            f"({format_bytes(downloaded)} / {format_bytes(total_size)}) "
                            f"{C_CYAN}{format_bytes(int(speed))}/s{C_RESET} ETA: {eta_str}  ",
                            end="",
                            flush=True
                        )

            print()  # 換行

        # 下載完成，重新命名回正式檔案
        temp_path.rename(target_path)
        return True

    except urllib.error.HTTPError as e:
        print(f"\n{C_RED}❌ HTTP 下載失敗 (狀態碼 {e.code}): {e.reason}{C_RESET}")
        return False
    except Exception as e:
        print(f"\n{C_RED}❌ 傳輸錯誤: {e}{C_RESET}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Cloudflare Clef-Flash GGUF 自動下載與安裝工具"
    )
    parser.add_argument(
        "--models-dir",
        type=str,
        default="./models",
        help="模型儲存目錄 (預設: ./models)"
    )
    parser.add_argument(
        "--hf-token",
        type=str,
        default=os.getenv("HF_TOKEN"),
        help="Hugging Face API Token (可選，加速或解除非公開下載限制)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="強制重新下載現有檔案"
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="僅校驗檔案完整性，不進行下載"
    )

    args = parser.parse_args()
    models_dir = Path(args.models_dir).resolve()

    print("=" * 66)
    print(f"{C_BOLD}{C_GREEN}🌟 Cloudflare Clef-Flash GGUF 模型自動部署安裝程式{C_RESET}")
    print("=" * 66)
    print(f"來源倉庫: {C_BOLD}https://huggingface.co/{REPO_ID}{C_RESET}")
    print(f"目標目錄: {C_BOLD}{models_dir}{C_RESET}\n")

    # 1. 檢查磁碟空間
    if not args.check_only:
        check_disk_space(models_dir, min_required_bytes=7500 * 1024 * 1024)

    # 2. 逐一處理模型下載與驗證
    all_success = True
    print(f"\n{C_BOLD}📦 準備檢查與下載模型檔案...{C_RESET}\n")

    for idx, item in enumerate(MODELS_CONFIG, 1):
        filename = item["filename"]
        target_file = models_dir / filename
        expected_size = item["expected_size"]
        desc = item["description"]
        url = f"{BASE_URL}/{filename}"

        print(f"[{idx}/{len(MODELS_CONFIG)}] {C_BOLD}{filename}{C_RESET}")
        print(f"    說明: {desc}")
        print(f"    預期大小: {format_bytes(expected_size)}")

        # 檢查是否已下載且大小吻合
        if target_file.exists():
            actual_size = target_file.stat().st_size
            if actual_size == expected_size and not args.force:
                print(f"    {C_GREEN}✅ 檔案已存在且大小完全吻合 ({format_bytes(actual_size)})，略過下載。{C_RESET}\n")
                continue
            elif actual_size != expected_size:
                print(f"    {C_YELLOW}⚠️ 檔案已存在但大小不符 (目前: {format_bytes(actual_size)})，準備重新下載...{C_RESET}")

        if args.check_only:
            print(f"    {C_RED}❌ 檔案缺失或不完整！{C_RESET}\n")
            all_success = False
            continue

        # 執行下載
        target_file.parent.mkdir(parents=True, exist_ok=True)
        download_success = False

        # 優先嘗試 huggingface_hub
        if download_with_hf_hub(REPO_ID, filename, target_file, token=args.hf_token):
            download_success = True
        else:
            download_success = download_with_urllib(url, target_file, expected_size, token=args.hf_token)

        # 驗證
        if target_file.exists() and target_file.stat().st_size == expected_size:
            print(f"    {C_GREEN}✅ 下載完成並通過大小驗證！{C_RESET}\n")
        else:
            print(f"    {C_RED}❌ 錯誤: 下載失敗或檔案大小不符！{C_RESET}\n")
            all_success = False

    print("=" * 66)
    if all_success:
        print(f"{C_GREEN}{C_BOLD}🎉 恭喜！所有 Clef-Flash GGUF 模型與多模態檔案已準備齊全！{C_RESET}")
        print("=" * 66)
        print(f"\n{C_BOLD}💡 下一步：啟動推論服務{C_RESET}")
        print(f"  方式 1 (Docker Compose):\n    {C_CYAN}docker compose up -d{C_RESET}")
        print(f"  方式 2 (原生 Python + llama-server):\n    {C_CYAN}pip install -r requirements.txt && python3 api_server.py{C_RESET}")
        print(f"  推論測試腳本:\n    {C_CYAN}python3 scripts/test_inference.py{C_RESET}\n")
        sys.exit(0)
    else:
        print(f"{C_RED}{C_BOLD}❌ 模型檔案檢查或下載未完成，請檢查網路後重新執行。{C_RESET}")
        print("=" * 66)
        sys.exit(1)


if __name__ == "__main__":
    main()
