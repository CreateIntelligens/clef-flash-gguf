import asyncio
import os
import sys
import time
from typing import Annotated, Any, Dict, List, Literal, Optional, Union
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, HTTPException, Request, status, BackgroundTasks
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
import uvicorn

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:18080")
INFLUX_URL = os.getenv("INFLUX_URL", "http://127.0.0.1:8086/api/v2/write?org=main&bucket=telegraf&precision=s")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
HOST_NAME = os.getenv("HOST_NAME", "clef-secondary")
MAX_CONCURRENT_INFERENCE = max(1, int(os.getenv("MAX_CONCURRENT_INFERENCE", "8")))
INFERENCE_QUEUE_TIMEOUT = max(0.1, float(os.getenv("INFERENCE_QUEUE_TIMEOUT", "10.0")))
BACKEND_TIMEOUT_SECONDS = max(1.0, float(os.getenv("BACKEND_TIMEOUT_SECONDS", "180.0")))
FlexibleValue = Union[str, Dict[str, Any], List[Any]]
StateValue = Union[str, Dict[str, Any], List[Any], None]

http_client: Optional[httpx.AsyncClient] = None
health_client: Optional[httpx.AsyncClient] = None
# 全域 Telemetry Client，避免每次都重建
telemetry_client: Optional[httpx.AsyncClient] = None
inference_semaphore: Optional[asyncio.Semaphore] = None


def _escape_influx_tag(value: str, max_len: int = 64) -> str:
    """Normalize tag values for InfluxDB line protocol safety."""
    return (
        value.replace("\\", "_")
        .replace(" ", "_")
        .replace(",", "_")
        .replace("=", "_")
        .replace("\n", "_")[:max_len]
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client, health_client, telemetry_client, inference_semaphore

    inference_limits = httpx.Limits(max_keepalive_connections=16, max_connections=32)
    http_client = httpx.AsyncClient(
        base_url=BACKEND_URL,
        timeout=httpx.Timeout(BACKEND_TIMEOUT_SECONDS, connect=5.0, pool=5.0),
        limits=inference_limits,
    )

    health_client = httpx.AsyncClient(
        base_url=BACKEND_URL,
        timeout=httpx.Timeout(2.0, connect=1.0),
        limits=httpx.Limits(max_keepalive_connections=2, max_connections=5),
    )

    telemetry_client = httpx.AsyncClient(
        timeout=httpx.Timeout(1.5, connect=1.0),
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
    )
    inference_semaphore = asyncio.Semaphore(MAX_CONCURRENT_INFERENCE)

    try:
        yield
    finally:
        for client in (http_client, health_client, telemetry_client):
            if client:
                await client.aclose()
        http_client = None
        health_client = None
        telemetry_client = None
        inference_semaphore = None


async def record_telemetry(
    status_code: int,
    latency: float,
    input_tokens: int = 0,
    questions_count: int = 0,
    choice_count: int = 0,
    noul_count: int = 0,
    score_count: int = 0,
    error_type: Optional[str] = None
):
    """非同步推送推論指標至 InfluxDB，避免創建過多同步連線。"""
    if not INFLUX_TOKEN or not telemetry_client:
        return
    try:
        status_type = "success" if status_code < 400 else ("client_error" if status_code < 500 else "server_error")
        safe_host = _escape_influx_tag(HOST_NAME)
        tags = f"host={safe_host},model=clef-flash,status={status_code},status_type={status_type}"
        if error_type:
            safe_err = _escape_influx_tag(error_type, max_len=32)
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
        await telemetry_client.post(
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
# Clef-Flash 決策 API（System One 相容）

由 Q4_K_M GGUF 與 llama.cpp 執行決策推論，支援 choice、noul、score。

- instructions 接受字串、JSON 物件或陣列；省略或 null 時以題目 ID 作為指示。
- choice criteria 接受 1–255 個選項，其描述可以是文字、物件、陣列或 null。
- noul criteria 可提供 true/false 的文字、物件或陣列描述。
- score criteria 接受 2–10 個由低至高的文字、物件或陣列等級；legend 保留原始描述。
- state 接受字串、JSON 物件、陣列或 null；images 可使用圖片 data URLs。

完整渲染 prompt 上限為 4096 tokens，包括 state、所有題目指示、criteria、
模型模板與圖片 token。context size 16384 與單次推論 batch 4096 是不同設定。
服務採單次聯合推論，呼叫端可依需求拆分獨立題組，各批保留必要情境。
Choice 需保留完整候選集合，分開候選後的機率分佈需另行設計比較策略。
超限回報由推論後端決定；選項數或字元數只能作為容量估計。

confidence 描述機率分佈集中程度，應依任務資料校準門檻。
串流輸出需另行驗證；此端點提供完整 JSON 回應。
"""

app = FastAPI(
    title="Cloudflare Clef-Flash Decision API",
    version="1.2.0",
    description=description,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# ----------------- Pydantic Schema 定義 -----------------

class PublicModel(BaseModel):
    model_config = ConfigDict(extra="allow")

class NoulQuestion(PublicModel):
    type: Literal["noul"] = "noul"
    instructions: FlexibleValue | None = Field(
        default=None, description="Yes/no question. If omitted, the question ID is used."
    )
    criteria: dict[str, FlexibleValue] | None = Field(
        default=None, description="Optional descriptions for true and false."
    )

class ChoiceQuestion(PublicModel):
    type: Literal["choice"] = "choice"
    instructions: FlexibleValue | None = None
    criteria: dict[str, Any] = Field(
        min_length=1,
        max_length=255,
        description="Map of option IDs to descriptions; use null when no description is needed.",
    )

class ScoreQuestion(PublicModel):
    type: Literal["score"] = "score"
    instructions: FlexibleValue | None = None
    criteria: list[FlexibleValue] = Field(
        min_length=2,
        max_length=10,
        description="Ordered score levels from lowest to highest.",
    )

Question = Annotated[Union[NoulQuestion, ChoiceQuestion, ScoreQuestion], Field(discriminator="type")]

class SystemOneRequest(PublicModel):
    model: Literal["clef-flash"] = Field(
        default="clef-flash",
        description="Model identifier. The service has Clef-Flash loaded.",
    )
    state: StateValue = Field(
        description="State to evaluate: string, object, array, or null."
    )
    questions: dict[str, Question] = Field(
        min_length=1,
        description=(
            "Questions keyed by caller-chosen IDs. Supports noul, choice, and score. "
            "Clef evaluates all questions jointly in one physical batch; the combined "
            "rendered request must stay within the 4096-token limit. The API does not "
            "split questions automatically."
        ),
    )
    images: list[str] | None = Field(
        default=None,
        description="Optional image data URLs, for example data:image/jpeg;base64,...",
    )

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "model": "clef-flash",
                    "state": {
                        "invoice": {
                            "vendor": "Acme",
                            "total": 1250.0,
                            "currency": "USD",
                            "status": "overdue",
                        }
                    },
                    "questions": {
                        "status": {
                            "type": "choice",
                            "instructions": "What is the invoice status?",
                            "criteria": {
                                "paid": "Invoice is paid.",
                                "overdue": "Invoice is past due.",
                                "draft": "Invoice was not sent.",
                            },
                        },
                        "large": {
                            "type": "noul",
                            "instructions": "Is the total above 1000 USD?",
                        },
                    },
                }
            ]
        },
    )

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
    confidence: float = Field(..., description="機率分佈集中程度，介於 0.0 與 1.0；與最高機率值分開解讀。")
    legend: Dict[str, FlexibleValue] = Field(..., description="等級索引與文字對照表")
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
    probe_client = health_client or http_client
    if probe_client:
        try:
            # 使用獨立的短 timeout 與獨立連線池，避免被推論請求塞爆連線池
            r = await probe_client.get("/health", timeout=2.0)
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
        'telemetry_target': 'InfluxDB Telegraf (configured server-side)'
    }

@app.post(
    '/v1/systemone',
    response_model=SystemOneResponse,
    summary="Clef-Flash 決策推論核心端點 (單次上限 4,096 Tokens，不自動分塊)",
    description=(
        "依 state 與 typed questions 執行聯合決策推論。instructions 與 criteria "
        "支援結構化 JSON；Score legend 保留等級描述的原始型別。images 使用 data URLs。"
        "完整 prompt（含模板與圖片 token）需在 4096-token 單次 batch 內；"
        "呼叫端可拆分獨立題組並保留每題所需情境與完整 Choice 候選集合。"
    ),
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

    req_dict = payload.model_dump(exclude_unset=True)
    req_dict["model"] = payload.model
    # 補齊 instructions（若使用者未設定，自動回退為題號 ID）
    for q_id, q_data in req_dict.get("questions", {}).items():
        if q_data.get("instructions") is None:
            q_data["instructions"] = str(q_id)

    acquired = False
    try:
        if not http_client:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="後端連線客戶端尚未就緒")

        if inference_semaphore is not None:
            try:
                await asyncio.wait_for(inference_semaphore.acquire(), timeout=INFERENCE_QUEUE_TIMEOUT)
                acquired = True
            except asyncio.TimeoutError:
                latency = round(time.time() - t_start, 3)
                print(
                    f"Inference queue full after {latency}s (max_concurrent={MAX_CONCURRENT_INFERENCE})",
                    file=sys.stderr,
                )
                background_tasks.add_task(
                    record_telemetry,
                    status_code=503,
                    latency=latency,
                    questions_count=questions_count,
                    choice_count=choice_count,
                    noul_count=noul_count,
                    score_count=score_count,
                    error_type="InferenceQueueFull"
                )
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={"detail": "推論佇列已滿，請稍後重試或切換備援節點"},
                    headers={"Retry-After": "5"},
                    background=background_tasks,
                )

        resp = await http_client.post("/v1/systemone", json=req_dict)
        latency = round(time.time() - t_start, 3)

        if resp.status_code != 200:
            err_text = resp.text
            error_status = resp.status_code
            # Physical batch overflow is an input/capacity issue, not an outage.
            if resp.status_code >= 500 and "is too large to process" in err_text and "physical batch size" in err_text:
                error_status = 400
            print(
                f"Backend HTTP {resp.status_code} -> {error_status} in {latency}s: {err_text[:300]}",
                file=sys.stderr,
            )
            background_tasks.add_task(
                record_telemetry,
                status_code=error_status,
                latency=latency,
                questions_count=questions_count,
                choice_count=choice_count,
                noul_count=noul_count,
                score_count=score_count,
                error_type=f"BackendHTTP_{resp.status_code}"
            )
            return JSONResponse(
                status_code=error_status,
                content={"detail": f"推論引擎回應異常: {err_text}"},
                background=background_tasks,
            )

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
        print(f"Backend request error in {latency}s: {type(re).__name__}: {re}", file=sys.stderr)
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
        detail = (
            "GGUF 後端服務請求逾時，請稍後重試"
            if isinstance(re, httpx.TimeoutException)
            else "GGUF 後端服務連線失敗，請稍後重試"
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": detail},
            background=background_tasks,
        )
    except Exception as e:
        latency = round(time.time() - t_start, 3)
        print(f"Unexpected inference error in {latency}s: {type(e).__name__}: {e}", file=sys.stderr)
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
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "推論執行發生內部錯誤，請稍後重試"},
            background=background_tasks,
        )
    finally:
        if acquired and inference_semaphore is not None:
            inference_semaphore.release()

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8099, timeout_graceful_shutdown=10)
