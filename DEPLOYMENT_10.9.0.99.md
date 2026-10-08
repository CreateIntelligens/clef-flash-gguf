# Clef-Flash 決策模型部署資訊 (主機 10.9.0.99 正式環境)

最後更新：2026-10-08  
狀態：生產運行中 (Production Active - Q4_K_M GGUF 量化加速版)

---

## 📌 1. 網路與服務架構拓樸

```text
瀏覽器／外部 API 客戶端
  └─ HTTPS https://clef.aiurl.tw:443 (Cloudflare Proxy CDN & SSL)
       └─ 入口反向代理 Nginx (10.9.0.35:443, Let's Encrypt TLS)
            └─ http://10.9.0.99:8099 (Clef-Flash FastAPI 門面 & Swagger)
                 │
                 ├─ 決策推論轉發 ──> http://127.0.0.1:18080/v1/systemone
                 │                     └─ Docker 容器 clef-flash-gguf-server
                 │                          └─ llama.cpp server (CPU AVX2)
                 │
                 └─ 遙測指標推送 ──> http://10.9.0.9:8008 (InfluxDB telegraf)
                                       └─ Grafana 看板: http://60.248.142.126:8007/d/clef-flash-metrics
```

- **對外主網域**：`https://clef.aiurl.tw`
- **中繼代理機**：`10.9.0.35`（監聽 80/443，終止 TLS 並轉發至 `10.9.0.99:8099`）
- **運算節點機**：`10.9.0.99`（主機名稱：`david-ubuntu-2404`）
- **監控節點機**：`10.9.0.9`（InfluxDB on 8008, Grafana on 8007 / 公網 `60.248.142.126:8007`）

---

## 💻 2. 主機規格與環境

- **主機 IP**：`10.9.0.99`
- **作業系統**：Ubuntu 24.04 LTS (Kernel 6.8.0)
- **處理器 (CPU)**：12th Gen Intel(R) Core(TM) i7-12700 (16 vCPUs, 支援 AVX2 / FMA 高速向量計算)
- **記憶體 (RAM)**：53 GB 總量（目前可用 ~42 GB；GGUF 推論僅佔約 3.7 GB）
- **儲存空間**：Root 分割區 348 GB（可用空間 117 GB）
- **服務工作目錄**：`/home/david/clef-flash-service`

> [!NOTE]
> 原 18 GB 之全精度 PyTorch safetensors 模型檔（`model/`）已於 2026-10-08 徹底刪除釋放磁碟空間，未來不再使用。

---

## 🧠 3. 模型與量化規格

- **模型架構**：Cloudflare Clef-Flash（基於 Qwen3.5-9B 架構之多模態 Joint Schema Head 決策專用模型）
- **來源倉庫**：[`bartowski/Cloudflare_clef-flash-GGUF`](https://huggingface.co/bartowski/Cloudflare_clef-flash-GGUF)
- **模型檔案路徑**：`/home/david/clef-flash-service/models/`
  1. `Cloudflare_clef-flash-Q4_K_M.gguf`（5.7 GB，4-bit Medium K-quant 量化）
  2. `mmproj-Cloudflare_clef-flash-f16.gguf`（876 MB，F16 多模態投影矩陣）
- **推論機制**：單次 Forward Pass 同時輸出所有選項機率，不進行逐字文字解碼（Non-autoregressive），無幻覺風險。

---

## 🐳 4. 推論引擎容器配置 (llama-server)

推論核心採用官方 llama.cpp 服務端容器 `ghcr.io/ggml-org/llama.cpp:server`，支援原生 Clef `/v1/systemone` 端點。

### 容器啟動指令

```bash
docker run -d \
  --name clef-flash-gguf-server \
  --restart unless-stopped \
  -v /home/david/clef-flash-service/models:/models:ro \
  -p 127.0.0.1:18080:8080 \
  ghcr.io/ggml-org/llama.cpp:server \
  -m /models/Cloudflare_clef-flash-Q4_K_M.gguf \
  --mmproj /models/mmproj-Cloudflare_clef-flash-f16.gguf \
  --host 0.0.0.0 \
  --port 8080 \
  -c 16384 \
  -b 4096 \
  -ub 4096 \
  -t 16 \
  -np 2
```

### 關鍵參數說明

| 參數 | 設定值 | 目的與技術細節 |
| :--- | :--- | :--- |
| `-c 16384 -np 2` | 總 Context 16,384，並行 2 Slots | 讓 2 個並行槽位各自擁有完整的 8,192 Context 空間，既支援多工並行又節省記憶體。 |
| `-b 4096 -ub 4096` | 實體 Batch 與 Micro-batch 4,096 | **核心關鍵設定**。Clef 需要單次 ubatch 處理完整輸入；若未設定會受限於預設 512 tokens，導致長文本報錯。目前實測 1,780 tokens 單次推論順利通過。 |
| `-t 16` | 16 Threads | 充分調用 i7-12700 之 16 個 CPU 核心進行並行推論。 |
| `--restart unless-stopped` | unless-stopped | 確保主機重啟後 Docker daemon 自動拉起容器。 |

---

## ⚡ 5. API 門面服務 (FastAPI & Systemd)

API 門面負責提供 OpenAPI 互動文件、Pydantic 欄位校驗、相容性回退、非同步請求轉發，以及遙測數據推送。

- **檔案路徑**：`/home/david/clef-flash-service/api_server.py`
- **虛擬環境**：`/home/david/clef-flash-service/.venv`
- **監聽端口**：`0.0.0.0:8099`

### Systemd 服務單元 (`/etc/systemd/system/clef-flash.service`)

```ini
[Unit]
Description=Cloudflare Clef-Flash Decision API Service
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
User=david
WorkingDirectory=/home/david/clef-flash-service
ExecStartPre=-/usr/bin/docker start clef-flash-gguf-server
ExecStart=/home/david/clef-flash-service/.venv/bin/python3 /home/david/clef-flash-service/api_server.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

### 相容性回退保護機制 (Compatibility Fallback)
llama.cpp 對 `/v1/systemone` 的輸入檢驗非常嚴格，每道題目均必須含有非空的 `instructions`。API 門面會在收到請求時，自動檢查題組：若用戶端省略了 `instructions`，會自動將其填入該題目的 ID（例如 `instructions = "urgency"`），保證所有舊版客戶端呼叫 100% 正常回傳。

---

## 🌐 6. Nginx 反向代理配置 (`10.9.0.35`)

檔案位置：`/etc/nginx/conf.d/clef.aiurl.tw.conf`

```nginx
server {
    server_name clef.aiurl.tw;

    location / {
        proxy_pass http://10.9.0.99:8099;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header CF-Connecting-IP $http_cf_connecting_ip;

        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }

    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/clef.aiurl.tw/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/clef.aiurl.tw/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
}

server {
    listen 80;
    server_name clef.aiurl.tw;
    if ($host = clef.aiurl.tw) {
        return 301 https://$host$request_uri;
    }
    return 404;
}
```

---

## 📊 7. 監控與儀表板 (InfluxDB & Grafana)

- **InfluxDB 端點**：`http://10.9.0.9:8008` (Org: `main`, Bucket: `telegraf`)
- **Measurement 名稱**：`clef_inference`
- **Grafana 儀表板網址**：
  - 外部連結：[http://60.248.142.126:8007/d/clef-flash-metrics](http://60.248.142.126:8007/d/clef-flash-metrics)
  - 內網連結：`http://10.9.0.9:8007/d/clef-flash-metrics`

### 儀表板指標與修正說明

1. **題目類型分佈 (Question Types Distribution)**：
   已更新 Flux 聚合投影，標籤已修正為友善中文分類：
   - `Choice (多選一)`
   - `Noul (是非題)`
   - `Score (等級評定)`
2. **成功率 (Success Rate)**：
   採用 `reduce` 計算 `(success / total) * 100`，精確顯示 0% ~ 100% 成功比例。
3. **推論延遲與吞吐量時間序列**：即時反映 P95/平均推論秒數與單次輸入 Tokens 消耗。

---

## 📡 8. 公開端點與 API 使用說明

| 方法 | 路徑 | 說明 |
| :--- | :--- | :--- |
| `GET` | `https://clef.aiurl.tw/docs` | 互動式 Swagger UI（支援線上測試 Try it out） |
| `GET` | `https://clef.aiurl.tw/redoc` | ReDoc API 規格文檔 |
| `GET` | `https://clef.aiurl.tw/openapi.json` | OpenAPI 3.1 規範 JSON |
| `GET` | `https://clef.aiurl.tw/health` | 伺服器與後端推論引擎健康狀態檢查 |
| `POST` | `https://clef.aiurl.tw/v1/systemone` | Clef-Flash 決策推論核心端點 |

### 推論範例 (cURL)

```bash
curl -X POST https://clef.aiurl.tw/v1/systemone \
  -H "Content-Type: application/json" \
  -d '{
    "model": "clef-flash",
    "state": "使用者反映於結帳步驟出現 HTTP 504 Gateway Timeout，信用卡款項已扣款但系統未產生訂單。",
    "questions": {
      "department": {
        "type": "choice",
        "instructions": "判斷此工單應指派給哪個部門處理？",
        "criteria": {
          "billing": "款項扣除、退款、對帳相關問題",
          "tech": "伺服器超時、API 錯誤、系統故障",
          "customer_support": "一般諮詢或常見問題"
        }
      },
      "urgency": {
        "type": "score",
        "instructions": "判斷此事件處理緊急程度",
        "criteria": ["可稍後處理", "今日處理", "即刻緊急處理 (P0)"]
      },
      "is_system_down": {
        "type": "noul",
        "instructions": "是否屬於核心服務中斷或重大故障？"
      }
    }
  }'
```

### 回應範例 (JSON)

```json
{
  "model": "clef-flash-q4_k_m",
  "answers": {
    "department": {
      "type": "choice",
      "choice": "tech",
      "confidence": 0.637,
      "probabilities": {
        "billing": 0.228,
        "customer_support": 0.014,
        "tech": 0.758
      }
    },
    "urgency": {
      "type": "score",
      "score": 1.905,
      "confidence": 0.857,
      "legend": {
        "0": "可稍後處理",
        "1": "今日處理",
        "2": "即刻緊急處理 (P0)"
      },
      "probabilities": {
        "0": 0.010,
        "1": 0.076,
        "2": 0.914
      }
    },
    "is_system_down": {
      "type": "noul",
      "noul": 0.481
    }
  },
  "usage": {
    "input_tokens": 372,
    "output_tokens": 0
  },
  "latency_seconds": 4.872
}
```

---

## 🔧 9. 維運常用指令

### 服務狀態檢查
```bash
ssh david@10.9.0.99 "docker ps | grep clef; sudo systemctl status clef-flash.service --no-pager"
```

### 檢查推論引擎日誌
```bash
ssh david@10.9.0.99 "docker logs --tail 50 -f clef-flash-gguf-server"
```

### 檢查 API 門面日誌
```bash
ssh david@10.9.0.99 "journalctl -u clef-flash.service -n 50 -f"
```

### 重啟服務
```bash
# 重啟 API 門面（會自動確保 docker 容器已運行）
ssh david@10.9.0.99 "sudo systemctl restart clef-flash.service"

# 重啟 GGUF 模型容器
ssh david@10.9.0.99 "docker restart clef-flash-gguf-server"
```

---

## 🛡️ 10. 實體 Batch Size 限制防禦與自動分塊機制 (Chunking Mechanism)

### 1. 物理上限與 Token 放大原理
- **底層限制**：`llama-server` 透過參數 `-b 4096 -ub 4096` 設定物理推論批次上限為 **4,096 Tokens**。
- **Token 放大效應**：
  在 Clef 的 System One 架構下，單次請求消耗的總 Tokens 會按「**題目數 × 選項描述長度**」倍數放大：
  $$\text{Total Tokens} \approx \text{Tokens}(\text{state}) + \sum_{q \in \text{questions}} (\text{Tokens}(\text{instructions}_q) + \sum_{opt \in criteria_q} \text{Tokens}(opt))$$
- **伺服器不會自動分塊 (No Auto-Chunking)**：
  - 伺服器端採單次 Forward Pass 同步計算整個 Joint Schema 的機率分佈。
  - **伺服器絕對不會在後端主動拆單或分塊**。
  - 若整包 Request 總 Prompt 超過 4,096 Tokens，推論引擎將直接報錯拒絕推論（HTTP 400/500 `input is too large to process`）。

### 2. 客戶端防禦建議 (Client-Side Chunking)
呼叫端（如 Jev 平台或業務前端）若需一次評估多個維度或長題組，**必須在客戶端實作分塊 (Chunking)** 機制：

```typescript
// 客戶端分塊邏輯範例 (按選項數自動切塊，杜絕溢出)
export function chunkQuestionsForClef(
  questions: Record<string, QuestionDef>,
  maxOptionsPerChunk: number = 10
): Array<Record<string, QuestionDef>> {
  const chunks: Array<Record<string, QuestionDef>> = [];
  let currentChunk: Record<string, QuestionDef> = {};
  let currentOptionsCount = 0;

  for (const [key, q] of Object.entries(questions)) {
    const optionCount = q.criteria ? (Array.isArray(q.criteria) ? q.criteria.length : Object.keys(q.criteria).length) : 2;
    if (currentOptionsCount + optionCount > maxOptionsPerChunk && Object.keys(currentChunk).length > 0) {
      chunks.push(currentChunk);
      currentChunk = {};
      currentOptionsCount = 0;
    }
    currentChunk[key] = q;
    currentOptionsCount += optionCount;
  }

  if (Object.keys(currentChunk).length > 0) {
    chunks.push(currentChunk);
  }
  return chunks;
}
```

- **連續推論與聚合**：
  客戶端切塊後，將各塊依序或並行送至 `/v1/systemone`，並在前端將回傳的 `answers` 合併為完整評估結果。
- **Swagger / OpenAPI 規範同步**：
  已將「單次請求上限 4,096 Tokens」與「伺服器端不自動分塊」完整寫入 Swagger UI、ReDoc 與 `/openapi.json` 的 `state`、`questions` 欄位描述與端點說明，方便整合者即時查閱。

