# ChatGPT-CDP → OpenAI API 橋接伺服器設計

## 目標

提供一個 OpenAI API 相容的 HTTP Server，讓任何呼叫 OpenAI API 的工具（如 ChatGPT UI、LM Studio、Ollama 等）可以無縫使用 Chrome CDP 自動化的 ChatGPT 作為後端。

## 架構

```
外部工具 (LM Studio / ChatGPT Web / 自訂客戶端)
        │
        │  POST /v1/chat/completions  (OpenAI format)
        ▼
┌─────────────────────┐
│  OpenAI API Server  │  ← 偵聽 port 8080
│  (FastAPI)          │
│                     │
│  • 解析 OpenAI req  │
│  • 建立 SSE stream  │
│  • 處理錯誤         │
└─────────┬───────────┘
          │
          │ 呼叫內部函式
          ▼
┌─────────────────────┐
│  CDP Backend        │  ← 維護單一 WebSocket 連線
│                     │
│  • 關閉登入彈窗      │
│  • 導航到 /new      │
│  • 逐字元輸入       │
│  • 智慧輪詢 --check │
│  • 擷取回覆文字      │
└─────────┬───────────┘
          │
          │  Chrome DevTools Protocol (WebSocket)
          ▼
┌─────────────────────┐
│  Chrome Browser     │
│  (port 9222)        │
│                     │
│  ChatGPT 未登入模式  │
└─────────────────────┘
```

## API 規格

### 端點

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | /health | 健康檢查，回傳 Chrome CDP 連線狀態 |
| GET | /v1/models | 列出可用的 model |
| POST | /v1/chat/completions | 主要端點，收發訊息 |

### POST /v1/chat/completions

**Request Format (完全相容 OpenAI)**

```json
{
  "model": "chatgpt-cdp",
  "messages": [
    {"role": "user", "content": "你好，請自我介紹"}
  ],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 2000
}
```

**Request Fields:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| model | string | no | "chatgpt-cdp" | 模型名稱 (任意值皆接受) |
| messages | array | yes | - | 對話歷史，取最後一個 user message |
| stream | bool | no | false | 是否使用 SSE 串流回應 |
| temperature | float | no | 1.0 | 溫度參數 (ChatGPT 不支援，保留供 API 相容) |
| max_tokens | int | no | 2000 | 最大 token 數 (ChatGPT 不支援，保留供 API 相容) |
| api_key | string | no | 任意 | API Key (為 API 相容接受任意值) |

**Response Format (非串流)**

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1716000000,
  "model": "chatgpt-cdp",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "我是 ChatGPT，基於 GPT-5.3-mini..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

**Response Format (串流 - SSE)**

```
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1716000000,"model":"chatgpt-cdp","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1716000000,"model":"chatgpt-cdp","choices":[{"index":0,"delta":{"content":"我是"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1716000000,"model":"chatgpt-cdp","choices":[{"index":0,"delta":{"content":" ChatGPT"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1716000000,"model":"chatgpt-cdp","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

**Error Response Format**

```json
{
  "error": {
    "message": "錯誤訊息",
    "type": "cdp_connection_error",
    "code": 500
  }
}
```

## 錯誤處理

| 錯誤情境 | HTTP Status | 處理方式 |
|----------|-------------|----------|
| Chrome CDP 無法連線 | 503 | 回傳 {"error": {...}} |
| 登入彈窗無法關閉 | 503 | 回傳 {"error": {...}} |
| ChatGPT 回覆逾時 | 504 | 回傳 {"error": {...}} |
| 訊息格式錯誤 | 400 | 回傳 {"error": {...}} |
| 服務端限流 (ChatGPT) | 200 | 回傳 ChatGPT 限流訊息 |

## 配置選項

| 變數 | 預設值 | 說明 |
|------|--------|------|
| PORT | 8080 | Server 偵聽埠口 |
| CDP_PORT | 9222 | Chrome CDP 埠口 |
| CHECK_INTERVAL | 2 | 智慧輪詢間隔 (秒) |
| MAX_WAIT | 120 | 最大等待秒數 |
| CHATGPT_URL | https://chatgpt.com/new | ChatGPT 頁面 URL |
| STRIP_HTML | true | 回應時移除 HTML 標籤 |
| MAX_MESSAGE_LENGTH | 5000 | 回應文字最大長度 |

## 檔案結構

```
chatgpt-cdp-server/
├── DESIGN.md              # 此文件
├── requirements.txt       # websocket-client, fastapi, uvicorn
├── chatgpt_cdp_backend.py # CDP 後端核心 (可 import 的模組)
└── server.py              # OpenAI API HTTP Server
```

## 啟動流程

```bash
# 安裝依賴
pip install fastapi uvicorn websocket-client

# 啟動伺服器
python server.py --port 8080

# 測試 (模擬 OpenAI API 呼叫)
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"chatgpt-cdp","messages":[{"role":"user","content":"你好"}]}'
```

## 測試方式

### 1. 直接 curl 測試

```bash
# 基本問答
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"1+1=?"}]}'

# 串流測試
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"寫一首短詩"}],"stream":true}'

# 健康檢查
curl http://localhost:8080/health

# 列出模型
curl http://localhost:8080/v1/models
```

### 2. 使用 OpenAI Python SDK 測試

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="anything"  # 任意值皆可
)

response = client.chat.completions.create(
    model="chatgpt-cdp",
    messages=[{"role": "user", "content": "你好"}]
)
print(response.choices[0].message.content)
```

### 3. 與 LM Studio / Ollama 等工具整合

在 LM Studio 的 "Custom Server" 設定中填入:
- Server URL: `http://localhost:8080/v1`
- API Key: `anything`

---

## 實作優先級

1. **P0**: OpenAI /v1/chat/completions 基本端點 (非串流)
2. **P1**: 串流支援 (SSE)
3. **P2**: /health 和 /v1/models
4. **P3**: 錯誤處理與重試機制
