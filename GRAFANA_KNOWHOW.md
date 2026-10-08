# Clef 決策模型 Grafana 監控實戰 Know-How 與踩坑全紀錄

最後更新：2026-10-08  
適用環境：FastAPI + InfluxDB 2.x (Flux) + Grafana 10/11 (Unified Storage)

---

## 📌 一、監控架構設計

```text
客戶端請求 ──> FastAPI 門面 (api_server.py:8099)
                  │
                  ├─ [主執行緒] 轉發推論並立即回傳給調用端 (毫秒級零阻塞)
                  │
                  └─ [背景執行緒 BackgroundTasks]
                        └─ HTTP POST Line Protocol (Timeout 2.0s)
                              │
                              ▼
                        InfluxDB 2.x (10.9.0.9:8008, Bucket: telegraf)
                              │
                              ▼
                        Grafana 看板 (10.9.0.9:8007 / 60.248.142.126:8007)
```

- **高可用設計原則**：監控指標推送絕不可干擾推論業務。即使 InfluxDB 當機或超時，API 仍必須正常回傳決策結果。
- **數據協議**：採用 InfluxDB Line Protocol 原生輕量協定，免裝重量級 SDK。

---

## 📦 二、Line Protocol 資料結構定義

```text
clef_inference,model=clef-flash,status=200,status_type=success latency_seconds=3.591,input_tokens=276i,questions_count=2i,choice_count=1i,noul_count=0i,score_count=1i
```

### 1. Measurement
- `clef_inference`

### 2. Tags（索引鍵，低基數）
| Tag 名稱 | 範例值 | 說明 |
| :--- | :--- | :--- |
| `model` | `clef-flash` | 固定模型標籤，供儀表板過濾 |
| `status` | `200`, `400`, `500` | HTTP 回應狀態碼 |
| `status_type` | `success`, `client_error`, `server_error` | 粗粒度狀態分類 |
| `error_type` | `BackendHTTP_400`, `BackendUnreachable` | 錯誤細部類型（僅出錯時附加） |

### 3. Fields（數值度量）
| Field 名稱 | 型態 | 語法規定 | 說明 |
| :--- | :--- | :--- | :--- |
| `latency_seconds` | Float | `3.591`（禁止加 i） | 整體推論耗時（秒） |
| `input_tokens` | Integer | `276i`（**必須加 i**） | 消耗之 Prompt Token 數 |
| `questions_count` | Integer | `2i`（**必須加 i**） | 該次請求之總題數 |
| `choice_count` | Integer | `1i`（**必須加 i**） | 多選一 (`choice`) 題數 |
| `noul_count` | Integer | `0i`（**必須加 i**） | 是非 (`noul`) 題數 |
| `score_count` | Integer | `1i`（**必須加 i**） | 評分 (`score`) 題數 |

---

## 📊 三、Grafana 儀表板完整 Flux 查詢庫

儀表板 UID：`clef-flash-metrics`  
連結：`http://60.248.142.126:8007/d/clef-flash-metrics`

### 1. 總決策調用數 (Total Calls) - `stat`
```flux
from(bucket: "telegraf")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "clef_inference" and r._field == "latency_seconds")
  |> count()
```

### 2. 成功率 (Success Rate) - `stat` (單位：`percent`)
```flux
from(bucket: "telegraf")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "clef_inference" and r._field == "latency_seconds")
  |> group()
  |> reduce(
      identity: {total: 0.0, success: 0.0},
      fn: (r, accumulator) => ({
          total: accumulator.total + 1.0,
          success: if r.status_type == "success" then accumulator.success + 1.0 else accumulator.success
      })
  )
  |> map(fn: (r) => ({
      _time: now(),
      _field: "success_rate",
      _value: if r.total > 0.0 then (r.success / r.total) * 100.0 else 100.0
  }))
```

### 3. 平均與 P95 推論延遲 - `stat` (單位：`s`)
```flux
// 平均延遲
from(bucket: "telegraf")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "clef_inference" and r._field == "latency_seconds" and r.status_type == "success")
  |> mean()

// P95 延遲
from(bucket: "telegraf")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "clef_inference" and r._field == "latency_seconds" and r.status_type == "success")
  |> quantile(q: 0.95)
```

### 4. 題目類型分佈 (Question Types Distribution) - `piechart` / `bargauge`
```flux
from(bucket: "telegraf")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "clef_inference")
  |> filter(fn: (r) => r._field == "choice_count" or r._field == "noul_count" or r._field == "score_count")
  |> group(columns: ["_field"])
  |> sum()
  |> map(fn: (r) => ({
      _field: if r._field == "choice_count" then "Choice (多選一)"
              else if r._field == "noul_count" then "Noul (是非題)"
              else if r._field == "score_count" then "Score (等級評定)"
              else r._field,
      _value: r._value
  }))
  |> keep(columns: ["_field", "_value"])
  |> group()
```

### 5. 推論耗時時間序列 (Inference Latency Trend) - `timeseries`
```flux
from(bucket: "telegraf")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "clef_inference" and r._field == "latency_seconds")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> yield(name: "mean_latency")
```

### 6. 近期推論詳細紀錄 (Recent Inference Records) - `table`
```flux
from(bucket: "telegraf")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "clef_inference")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> group()
  |> keep(columns: ["_time", "status", "latency_seconds", "questions_count", "choice_count", "noul_count", "score_count", "input_tokens"])
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: 25)
```

---

## 💥 四、踩坑全實錄與解決之道 (Pitfalls & Solutions)

### 坑 1：成功率面板顯示「4%」幽靈數字
- **現象**：系統剛上線時 4 次呼叫全部成功，但面板上的成功率卻顯示「4%」（綠字），而非 100%。
- **原因**：
  查詢原本寫為 `count()`（計算成功筆數 = 4），但面板設定將 Unit 指定為 `Percent (0-100)`。Grafana 看到數值是 4，直接將其渲染成 `4%`！
- **解法**：
  在 Flux 中使用 `reduce()` 同時累加 `total` 與 `success`，並使用 `map()` 計算出真實的百分比數值：
  `(success / total) * 100.0`。

---

### 坑 2：圓餅圖/柱狀圖標籤被污染成 `_value {_field="choice_count"...}`
- **現象**：
  題目類型分佈面板顯示極為雜亂的標籤：
  `_value {_field="choice_count", _start="2026-...", _stop="2026-...", status="200"} 1182`
- **原因**：
  Flux 預設回傳的資料結構包含時間窗口 (`_start`, `_stop`)、分組鍵與標籤。當 Grafana 收到多個標籤維度時，會自動組合成複雜的 Series 名稱。
- **解法**：
  在 Flux 語法最後加上兩道過濾步驟：
  1. `|> keep(columns: ["_field", "_value"])`：徹底拋棄 `_start`、`_stop` 與所有冗餘 tag。
  2. `|> group()`：將所有資料表合併回單一無分組表格。
  這樣 Grafana 就只會拿到乾淨的 `_field` 與 `_value`，自動呈現優雅的中文標籤。

---

### 坑 3：表格欄位標題出現 `tus_type="success"}` 殘留字串
- **現象**：
  Table 面板表頭本應顯示「時間」、「HTTP 狀態」、「推論延遲」，卻變成帶有括號結尾的 tag 碎片字串。
- **原因**：
  在 `pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")` 之前，資料庫中帶有多個 Tag（例如 `status_type`）。如果未在 pivot 前後執行 `group()` 解除 Tag 分組，pivot 出來的每一欄都會附帶該 Tag 作為標頭。
- **解法**：
  在 pivot 之後立即加上 `|> group()`，並使用 `keep(columns: [...])` 明確指定所需的純淨欄位清單。

---

### 坑 4：Line Protocol 型態不符導致資料庫靜默丟棄 (Dropped Points)
- **現象**：
  API 端送出推論紀錄，但在 InfluxDB 中始終查不到 `input_tokens` 或題數。
- **原因**：
  InfluxDB 對欄位型態要求極度嚴格。如果在同一 measurement 中，首次寫入的值沒有後綴，InfluxDB 會判定該欄位為 Float；後續若程式寫入 Integer 或相反，整筆資料會被視為型態衝突而直接默默丟棄。
- **解法**：
  - 整數計數型欄位（Tokens、題數），在字串拼接時**一律明確加上 `i`**（如 `input_tokens=372i`）。
  - 浮點數（延遲秒數）絕對**不可加 `i`**（如 `latency_seconds=4.872`）。

---

### 坑 5：Grafana 11+ Unified Storage 無法透過傳統 `dashboard` 資料庫表修改
- **現象**：
  連入 Grafana 的 SQLite 資料庫（`grafana.db`），執行 `SELECT * FROM dashboard` 發現空空如也（0 rows），但網頁明明看得到儀表板。
- **原因**：
  Grafana 11+ 全面改用統一儲存架構（Unified Storage）。儀表板不再存放在舊的 `dashboard` 表，而是存放在 **`resource`** 表（`group='dashboard.grafana.app'`, `resource='dashboards'`），其 JSON 定義存於 `value` 欄位中。
- **解法**：
  若需手動透過 SQLite 批量調整面板或修復 Flux 查詢：
  ```sql
  SELECT value FROM resource WHERE name = 'clef-flash-metrics';
  UPDATE resource SET value = ? WHERE name = 'clef-flash-metrics';
  ```
  更新後重啟 Grafana 容器（`docker restart <grafana-container>`）即可立即生效。

---

### 坑 6：FastAPI 連線池阻塞推論回應
- **現象**：
  原本推論只需 3 秒，但當監控端 InfluxDB 負載高或網路稍慢時，調用端響應時間被拖慢至 5~6 秒。
- **原因**：
  若直接在 FastAPI 路由函式中同步執行 `requests.post()` 到 InfluxDB，會阻塞整個 API 的請求週期。
- **解法**：
  使用 FastAPI 原生的 `BackgroundTasks`，並將推送連線設定強硬的逾時限制（2 秒）：
  ```python
  background_tasks.add_task(
      record_telemetry,
      status_code=200,
      latency=latency,
      input_tokens=input_tokens,
      ...
  )
  ```
  讓推論結果第一時間以 200 OK 回給調用端，監控指標則由背景 worker 默默推送。

---

### 坑 7：圓餅圖 (Piechart) 顯示單一顏色 100% 圈圈與 `_value 98`
- **現象**：
  題目類型分佈面板明明有三種題型，畫面卻只畫出一個黃色圈圈，標籤顯示 `_value 98 100%`，其餘題型完全不見。
- **原因**：
  Flux 查詢若輸出多行記錄（例如三行：Choice=375、Noul=11、Score=98），而 Grafana Piechart 面板預設的 `reduceOptions.values` 為 `false`，且 `calcs` 設定為 `lastNotNull`。
  此時 Grafana 不會把每一「行」畫成扇區，而是對 `_value` 這一欄所有列進行聚合計算法（取最後一列 = 98），導致整張圓餅圖只剩下 Score 的 98，並將欄位名 `_value` 當作圖例標籤！
- **解法**：
  在 Flux 語法中，將三種題型透過 `pivot()` 轉為**獨立欄位（Columns）**：
  ```flux
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> drop(columns: ["_time", "_start", "_stop"])
  ```
  輸出為單列三欄：`Choice (多選一)`、`Noul (是非題)`、`Score (等級評定)`。Grafana 便會自動將三個欄位渲染為三塊獨立扇區，顏色與中文標籤完美分離。

---

### 坑 8：多主機環境缺乏主機標籤與切換變數 (Multi-host Filter)
- **現象**：
  系統同時運行 `10.9.0.99` (CPU) 與 `10.9.0.32` (GPU)，但 Grafana 面板全部混在一起，且畫面上方沒有任何下拉選單可以篩選特定主機。
- **原因**：
  1. API 門面在推送 Line Protocol 時未包含 `host` Tag。
  2. 只有單一主機送指標，另一台主機（`10.9.0.32`）完全未配置遙測推送。
  3. Grafana 儀表板缺少 `$host` 模板變數（Template Variable），查詢也未作主機條件過濾。
- **解法**：
  1. 兩台 API 門面均在 Line Protocol 中加入 `host=10.9.0.99` 與 `host=10.9.0.32`。
  2. 在 Grafana 儀表板定義 Custom 變數 `host`（選項包含 `All`、`10.9.0.99`、`10.9.0.32`）。
  3. 各面板 Flux 查詢加入動態判斷式（兼顧舊資料向下相容）：
     ```flux
     |> filter(fn: (r) =>
         if "${host}" == "All" or "${host}" == "" then true
         else if "${host}" == "10.9.0.99" then (not exists r.host or r.host == "10.9.0.99")
         else r.host == "${host}"
     )
     ```
  4. 詳細明細表中加入「主機 (Host)」欄位，一眼辨識每一筆請求的承接節點。

---

### 坑 9：Flux pivot() 報錯「specified row key column does not exist in table: _time」
- **現象**：
  圓餅圖面板左上角出現紅色驚嘆號，畫面顯示「No data」，Grafana 後台日誌噴出：
  `err="invalid: specified row key column does not exist in table: _time"`
- **原因**：
  在 Flux 語法中：
  ```flux
  |> group(columns: ["_field"])
  |> sum()
  ```
  當使用 `group(columns: ["_field"])` 重新分組時，原有的 `_start` 與 `_stop` 欄位並不在分組鍵內，隨後的 `sum()` 聚合函式會**直接丟棄 `_start` 與 `_stop` 欄位**。
  若此時下游 `map()` 寫了 `_time: r._stop`，因為 `r._stop` 根本不存在，導致 `_time` 欄位為空/不存在。緊接著執行 `pivot(rowKey: ["_time"])` 時，Flux 就會拋出 400 錯誤 `specified row key column does not exist in table: _time`！
- **解法**：
  不要依賴被丟棄的時間欄位，直接在 `map()` 中指定一個確定的固定列識別鍵（例如 `row: "summary"`），並加上 `group()` 解除欄位分組後再 pivot：
  ```flux
  |> map(fn: (r) => ({
      row: "summary",
      _field: if r._field == "choice_count" then "Choice (多選一)"
              else if r._field == "noul_count" then "Noul (是非題)"
              else if r._field == "score_count" then "Score (等級評定)"
              else r._field,
      _value: r._value
  }))
  |> group()
  |> pivot(rowKey: ["row"], columnKey: ["_field"], valueColumn: "_value")
  |> drop(columns: ["row"])
  ```
  這樣保證 100% 存在 rowKey，且在各主機切換（`All`、`10.9.0.99`、`10.9.0.32`）時都能穩定回傳 200 OK。


