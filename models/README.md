# Models Directory

放置 Clef-Flash GGUF 量化模型與多模態投影矩陣檔案：

1. `Cloudflare_clef-flash-Q4_K_M.gguf` (約 5.7 GB)
2. `mmproj-Cloudflare_clef-flash-f16.gguf` (約 876 MB)

> 💡 **注意事項**：模型檔案體積龐大，已被 `.gitignore` 排除，**請勿**直接提交至 Git。

### 📥 自動下載方式 (任選一種)

#### 方式 1：一鍵全自動安裝腳本 (推薦，支援斷點續傳與大小校驗)
```bash
# 在專案根目錄執行
./install.sh

# 或跨平台 Python 執行
python3 install.py
```

#### 方式 2：使用純 Bash 下載腳本
```bash
bash scripts/download_models.sh
```

#### 方式 3：使用 Hugging Face 官方 CLI
```bash
huggingface-cli download bartowski/Cloudflare_clef-flash-GGUF \
  Cloudflare_clef-flash-Q4_K_M.gguf \
  mmproj-Cloudflare_clef-flash-f16.gguf \
  --local-dir ./models
```

