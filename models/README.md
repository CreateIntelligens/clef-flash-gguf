# Models Directory

放置 Clef-Flash GGUF 量化模型與多模態投影矩陣檔案：

1. `Cloudflare_clef-flash-Q4_K_M.gguf` (約 5.7 GB)
2. `mmproj-Cloudflare_clef-flash-f16.gguf` (約 876 MB)

可直接執行根目錄之下載腳本自動抓取：

```bash
bash scripts/download_models.sh
```

或使用 Hugging Face CLI：

```bash
huggingface-cli download bartowski/Cloudflare_clef-flash-GGUF \
  Cloudflare_clef-flash-Q4_K_M.gguf \
  mmproj-Cloudflare_clef-flash-f16.gguf \
  --local-dir ./models
```
