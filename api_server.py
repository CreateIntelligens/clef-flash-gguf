import os
import sys
import time
from typing import Any, Dict, List, Literal, Optional, Union
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, HTTPException, Request, status, BackgroundTasks
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
import uvicorn

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:18080")
INFLUX_URL = os.getenv("INFLUX_URL", "http://10.9.0.9:8008/api/v2/write?org=main&bucket=telegraf&precision=s")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "7f291e7ddcfa80d05deb46d3eb6137b54553e72241378f3c")
HOST_NAME = os.getenv("HOST_NAME", "10.9.0.99")

http_client: Optional[httpx.AsyncClient] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(
        base_url=BACKEND_URL,
        timeout=httpx.Timeout(180.0, connect=5.0)
    )
    yield
    if http_client:
        await http_client.aclose()

def record_telemetry(
    status_code: int,
    latency: float,
    input_tokens: int = 0,
    questions_count: int = 0,
    choice_count: int = 0,
    noul_count: int = 0,
    score_count: int = 0,
    error_type: Optional[str] = None
):
    """背景線程推送指標至 10.9.0.9 InfluxDB，不阻塞 API 回應。"""
    try:
        status_type = "success" if status_code < 400 else ("client_error" if status_code < 500 else "server_error")
        tags = f"host={HOST_NAME},model=clef-flash,status={status_code},status_type={status_type}"
        if error_type:
            safe_err = error_type.replace(" ", "_").replace(",", "_")[:32]
            tags += f",error_type={safe_err}"
        fields = (
            f"latency_seconds={latency},"
            f"input_tokens={input_tokens}i,"
            f"questions_count={questions_count}i,"
            f"choice_count={choice_count}i,"
            f"noul_count={noul_count}i,"
            f"score_count={score_count}i"
        )
        line = f"clef_inference,{tags} {fields}"
        with httpx.Client(timeout=2.0) as client:
            client.post(
                INFLUX_URL,
                headers={
                    "Authorization": f"Token {INFLUX_TOKEN}",
                    "Content-Type": "text/plain; charset=utf-8"
                },
                content=line.encode("utf-8")
            )
    except Exception as e:
        print(f"Telemetry error: {e}", file=sys.stderr)

description = """
# Cloudflare Clef-Flash 決策模型 API (SystemOne 相容規格 - GGUF 加速版)

Clef-Flash 是 Cloudflare 基於 Qwen3.5-9B 開發的多模態「決策專用模型」。
與傳統 LLM 逐字輸出（Autoregressive）不同，Clef-Flash 透過 Joint Schema Head 在**單次 Forward Pass** 中直接對所有問題選項計算機率分佈，無須解析文字或處理格式幻覺。

本服務已升級採用 **Q4_K_M GGUF 量化模型 + llama.cpp 核心引擎**，推論延遲顯著優化降低（降低約 80%~90%），同時保持極高準確度。

---

## 📌 支援的三種題目類型 (Question Types)

1. **`choice`（多選一分類）**：
   - 用於單選分類任務（如部門派工、工單分類、事件類型判定）。
   - **必填 `criteria`**：格式為字典 `{"選項ID": "選項描述"}`。
   - 回傳：選出的 `choice`、信心度 `confidence` 以及所有選項的 `probabilities` 機率分佈。

2. **`noul`（二分判斷 / 是非題）**：
   - 用於是非、布林、是否核准判定（True / False）。
   - 可選填 `criteria`（預設會以 `true`/`false` 判斷）。
   - 回傳：`noul`（代表判定為 `true` 的機率值，介於 0.0 ~ 1.0）。

3. **`score`（等級評定 / 有序評分）**：
   - 用於有先後順序的等級、優先級、緊急度評等（如優先級 P0~P3、滿意度 1~5 星）。
   - **必填 `criteria`**：格式為有序字串列表，索引由低至高排列，如 `["低", "中", "高", "緊急"]`。
   - 回傳：加權期望分數 `score`、最高機率等級 `confidence` 與各等級機率 `probabilities`。

---

## ⚠️ 實體 Token 限制與分塊機制規範 (Important Limits & Chunking)

- **單次請求實體上限 (Physical Token Limit)**：**4,096 Tokens**。
  - 後端推論引擎實體批次大小設定為 `-b 4096 -ub 4096`。
- **伺服器「不會自動分塊」(No Server-Side Auto-Chunking)**：
  - Clef-Flash 模型架構採用單次前向傳播 (Single Forward Pass) 同步計算所有選項的聯合分佈 (Joint Schema)，伺服器**不會**替客戶端自動切塊。
  - 單次請求總 Token 數（由 `state` 情境描述 + 所有題目 `instructions` + 各選項 `criteria` 說明加總）若超過 **4,096 Tokens**，底層推論引擎將直接拒絕處理並觸發報錯（HTTP 400/500 `input is too large to process`）。
- **客戶端分塊建議 (Client Chunking Practice)**：
  - 若題組龐大、選項眾多（Token 消耗會隨「題目數 × 選項數」倍數放大）或情境文本極長，呼叫端**必須在客戶端實作分塊 (Chunking)** 機制（例如：限制單次請求 ≤ 10 個選項，或按業務維度切分為多次請求發送），並在客戶端聚合結果。

---

## 💡 輸入資料格式 (State)
`state` 欄位可傳入任何情境描述，支援：
- 純文字字串（客戶訊息、日誌內容、事件通報）
- JSON 字典物件（結構化資料，模型會自動序列化讀取）
"""

app = FastAPI(
    title="Cloudflare Clef-Flash Decision API",
    version="1.1.0",
    description=description,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# ----------------- Pydantic Schema 定義 -----------------

class QuestionDef(BaseModel):
    type: Literal["choice", "noul", "score"] = Field(
        ...,
        description="問題類型：\n- `choice`: 多選一類別\n- `noul`: 二分是非判斷 (回傳 True 的機率)\n- `score`: 有序等級評分 (由低至高)"
    )
    instructions: Optional[str] = Field(
        None,
        description="問題說明或判定指示。若省略，則自動使用 question ID 作為預設指示。"
    )
    criteria: Optional[Union[Dict[str, str], List[str]]] = Field(
        None,
        description=(
            "選項判定準則：\n"
            "- 若 type 為 `choice`，必須為字典：`{'選項ID': '判定標準說明'}`\n"
            "- 若 type 為 `score`，必須為有序列表：`['等級0說明', '等級1說明', ...]`\n"
            "- 若 type 為 `noul`，此欄位可省略\n\n"
            "⚠️ **Token 消耗提示**：每個選項的描述文字皆會計入單次請求 4,096 Tokens 的實體上限，請保持精簡明確，避免冗長無關內容。"
        )
    )

class SystemOneRequest(BaseModel):
    model: str = Field(
        default="clef-flash",
        description="模型識別代碼，固定為 `clef-flash`"
    )
    state: Union[str, Dict[str, Any], Any] = Field(
        ...,
        description=(
            "待評估的情境、工單內容、客訴描述或結構化 JSON 資料。\n\n"
            "⚠️ **單次上限 4,096 Tokens 與無自動分塊**：\n"
            "- 本欄位與所有題目說明、選項 criteria 共同計入單次 **4,096 Tokens** 實體上限。\n"
            "- 伺服器端**不會自動進行分塊 (No Auto-Chunking)**。若文本過長，請先於客戶端進行摘要或切塊後再送出請求。"
        )
    )
    questions: Dict[str, QuestionDef] = Field(
        ...,
        description=(
            "題組字典，Key 為自訂題目代號（例如 'department', 'urgency'），Value 為題目結構定義。\n\n"
            "⚠️ **Token 放大效應與分塊建議**：\n"
            "- 單次請求消耗之 Token 數會隨「題目數 × 選項數」倍數放大，全請求上限為 **4,096 Tokens**。\n"
            "- 伺服器**不會**在超出上限時自動分割請求。\n"
            "- 若評估維度或題數較多（例如 > 10 個選項或多題複雜評估），呼叫端應於客戶端實裝自動分塊（如每批次 ≤ 10 個選項切塊連續呼叫後聚合）。"
        )
    )

    model_config = {
        "json_schema_extra": {
            "example": {
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
            }
        }
    }

class ChoiceAnswer(BaseModel):
    type: Literal["choice"] = "choice"
    choice: str = Field(..., description="模型選出的最高機率選項 ID")
    confidence: float = Field(..., description="該選項的預測信心度 (0.0 ~ 1.0)")
    probabilities: Dict[str, float] = Field(..., description="各選項的完整機率分佈")

class NoulAnswer(BaseModel):
    type: Literal["noul"] = "noul"
    noul: float = Field(..., description="判定為 True 的機率值 (0.0 ~ 1.0)")

class ScoreAnswer(BaseModel):
    type: Literal["score"] = "score"
    score: float = Field(..., description="加權期望評分（以索引 0 ~ N-1 加權計算）")
    confidence: float = Field(..., description="最高機率等級的信心度")
    legend: Dict[str, str] = Field(..., description="等級索引與文字對照表")
    probabilities: Dict[str, float] = Field(..., description="各等級的機率分佈")

class UsageInfo(BaseModel):
    input_tokens: int = Field(..., description="輸入消耗之 Token 數")
    output_tokens: int = Field(0, description="輸出 Token 數（Clef 為單次 Forward，固定為 0）")

class SystemOneResponse(BaseModel):
    model: str = Field(..., description="模型識別碼")
    answers: Dict[str, Union[ChoiceAnswer, NoulAnswer, ScoreAnswer, Dict[str, Any]]] = Field(
        ...,
        description="各題目的決策結果，Key 與請求中的題目代號相對應"
    )
    usage: UsageInfo = Field(..., description="Token 統計資訊")
    latency_seconds: float = Field(..., description="伺服器推論耗時（秒）")

# ----------------- 端點 -----------------

@app.get('/', include_in_schema=False)
def root():
    return RedirectResponse(url='/docs')

@app.get('/health', summary="健康檢查端點", tags=["監控與健康狀態"])
async def health():
    backend_status = "unreachable"
    if http_client:
        try:
            r = await http_client.get("/health")
            if r.status_code == 200:
                backend_status = "healthy"
        except Exception:
            backend_status = "unreachable"

    return {
        'status': 'healthy' if backend_status == 'healthy' else 'degraded',
        'backend_status': backend_status,
        'engine': 'llama.cpp server',
        'device': 'cpu',
        'dtype': 'Q4_K_M (4-bit GGUF)',
        'model': 'bartowski/Cloudflare_clef-flash-GGUF',
        'api_format': 'SystemOne / Jev Compatible',
        'telemetry_target': '10.9.0.9:8008 (InfluxDB telegraf)'
    }

@app.post(
    '/v1/systemone',
    response_model=SystemOneResponse,
    summary="Clef-Flash 決策推論核心端點 (單次上限 4,096 Tokens，不自動分塊)",
    description="""
接收情境描述 (`state`) 與題組定義 (`questions`)，執行單次 Forward Pass 決策並回傳所有題目的機率分佈與結果。

### ⚠️ 重要限制與整合規範 (Important Constraints & Best Practices)：
1. **單次請求實體上限 4,096 Tokens**：
   - 總 Token 計算方式為：`state` 文本 + 所有題目的 `instructions` + 各選項 `criteria` 說明加總。
   - 後端推論引擎物理批次大小設定為 `-b 4096 -ub 4096`，單次推論之總 Prompt 絕對不得超過 4,096 Tokens。
2. **伺服器「不會自動分塊」(No Server-Side Automatic Chunking)**：
   - Clef 模型為單次前向傳播架構，伺服器**不會主動**將過大或超長之請求自動切塊處理。
   - 若超出 4,096 Tokens 限制，底層推論引擎將直接報錯拒絕推論（HTTP 400/500 `input is too large to process`）。
3. **客戶端分塊建議 (Client-Side Chunking)**：
   - 若有大量題型、大量選項或長文本評估需求（Token 消耗會隨「題目數 × 選項數」倍數放大），呼叫端**必須於客戶端實作分塊 (Chunking)** 機制（例如按選項數切塊，每次呼叫 ≤ 10 個選項），連續發送後由客戶端自動聚合結果。
""",
    tags=["決策推論 (Decision Inference)"],
    status_code=status.HTTP_200_OK,
    responses={
        400: {"description": "輸入格式或題型參數錯誤，或超出單次 Token 上限 (Bad Request)"},
        422: {"description": "請求欄位驗證失敗 (Unprocessable Entity)"},
        500: {"description": "推論伺服器內部錯誤 (Internal Error)"}
    }
)
async def post_systemone(payload: SystemOneRequest, background_tasks: BackgroundTasks):
    """
    接收情境描述 (`state`) 與題組定義 (`questions`)，執行單次 Forward Pass 決策並回傳所有題目的機率分佈與結果。
    
    支援題型：
    - `choice`：多分類
    - `noul`：二分是非題
    - `score`：等級/優先級排序
    """
    t_start = time.time()
    choice_count = sum(1 for q in payload.questions.values() if q.type == "choice")
    noul_count = sum(1 for q in payload.questions.values() if q.type == "noul")
    score_count = sum(1 for q in payload.questions.values() if q.type == "score")
    questions_count = len(payload.questions)

    if questions_count == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one question is required")

    req_dict = payload.model_dump(exclude_none=True)
    # 補齊 instructions（若使用者未設定，自動回退為題號 ID）
    for q_id, q_data in req_dict.get("questions", {}).items():
        if not q_data.get("instructions"):
            q_data["instructions"] = str(q_id)

    try:
        if not http_client:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="後端連線客戶端尚未就緒")

        resp = await http_client.post("/v1/systemone", json=req_dict)
        latency = round(time.time() - t_start, 3)

        if resp.status_code != 200:
            err_text = resp.text
            background_tasks.add_task(
                record_telemetry,
                status_code=resp.status_code,
                latency=latency,
                questions_count=questions_count,
                choice_count=choice_count,
                noul_count=noul_count,
                score_count=score_count,
                error_type=f"BackendHTTP_{resp.status_code}"
            )
            raise HTTPException(status_code=resp.status_code, detail=f"推論引擎回應異常: {err_text}")

        result = resp.json()
        result["model"] = "clef-flash-q4_k_m"
        result["latency_seconds"] = latency
        input_tokens = result.get("usage", {}).get("input_tokens", 0)

        # 非同步推送到 InfluxDB
        background_tasks.add_task(
            record_telemetry,
            status_code=200,
            latency=latency,
            input_tokens=input_tokens,
            questions_count=questions_count,
            choice_count=choice_count,
            noul_count=noul_count,
            score_count=score_count
        )
        return result

    except HTTPException:
        raise
    except httpx.RequestError as re:
        latency = round(time.time() - t_start, 3)
        background_tasks.add_task(
            record_telemetry,
            status_code=503,
            latency=latency,
            questions_count=questions_count,
            choice_count=choice_count,
            noul_count=noul_count,
            score_count=score_count,
            error_type="BackendUnreachable"
        )
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"GGUF 後端服務連線失敗: {str(re)}")
    except Exception as e:
        latency = round(time.time() - t_start, 3)
        background_tasks.add_task(
            record_telemetry,
            status_code=500,
            latency=latency,
            questions_count=questions_count,
            choice_count=choice_count,
            noul_count=noul_count,
            score_count=score_count,
            error_type=type(e).__name__
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"推論執行異常: {str(e)}")

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8099)
