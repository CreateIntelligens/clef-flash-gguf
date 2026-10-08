#!/usr/bin/env python3
"""
Clef-Flash API 推論基準測試與功能驗證腳本
"""
import sys
import time
import json
import urllib.request

API_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8099/v1/systemone"

payload = {
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

print(f"正在測試目標端點: {API_URL} ...")
data = json.dumps(payload).encode("utf-8")
req = urllib.request.Request(
    API_URL,
    data=data,
    headers={"Content-Type": "application/json"}
)

t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        duration = time.time() - t0
        body = resp.read().decode("utf-8")
        result = json.loads(body)
        print(f"✅ 請求成功 (耗時: {duration:.3f} 秒 / HTTP {resp.status})")
        print(json.dumps(result, indent=2, ensure_ascii=False))
except urllib.error.HTTPError as e:
    duration = time.time() - t0
    print(f"❌ HTTP 錯誤 (耗時: {duration:.3f} 秒 / HTTP {e.code}): {e.read().decode('utf-8')}")
    sys.exit(1)
except Exception as e:
    duration = time.time() - t0
    print(f"❌ 連線異常 (耗時: {duration:.3f} 秒): {e}")
    sys.exit(1)
