---
name: chatgpt-unauthenticated-cdp
category: devops
description: 使用 Chrome 9222 CDP 在未經登入的 ChatGPT 頁面中對話 — 自動偵測並關閉登入彈窗，無需帳號。
---

# ChatGPT 未登入模式 — Chrome 9222 CDP 使用流程

## 情境說明

Chrome 9222 開啟 ChatGPT 時，會顯示「尚未登入」彈窗（有登入 / 免費註冊按鈕）。
**不需要登入** — 工具會自動偵測並關閉彈窗，直接使用輸入框開始對話。

## CLI 工具

```
python3 chatgpt_cdp.py <command> [args]

Commands:
  status                     檢查頁面狀態
  type <msg>                 在輸入框打字（不送出）
  ask <msg>                  打字 + 送出 + 等待回覆
  ask <msg> --new            先導航到 /new（清除歷史）再問
  ask <msg> --check <sec>    每 n 秒檢查一次答案更新 (default: 2)
  ask <msg> --wait <sec>     最大等待秒數 (default: 120, 已棄用)
  ask <msg> --screenshot <f> 打字 + 送出 + 截圖
  history [lines]            取得對話歷史文字
  close-modal                提示使用者手動關閉彈窗
  tabs                       列出所有分頁
  goto <url>                 導航到指定 URL
  eval <js_code>             執行 JavaScript
  screenshot <file>          截圖
  html <file>                儲存頁面 HTML
```

## 完整步驟

### Step 1: 確認 Chrome 9222 連線

```bash
curl -s http://127.0.0.1:9222/json
```

回傳 tab 清單，確認有 ChatGPT 分頁。

### Step 2: 確認頁面狀態（自動關閉登入窗）

```bash
python3 chatgpt_cdp.py status
```

工具會自動偵測「尚未登入」彈窗並關閉它。
成功標誌：頁面顯示「隨時準備好就可以開始了。」+ 輸入框。

### Step 3: 對話

```bash
python3 chatgpt_cdp.py ask "你的問題"
python3 chatgpt_cdp.py ask "Write a Python bubble sort" --screenshot /tmp/sort.jpg
python3 chatgpt_cdp.py ask "新問題" --new              # 先清除對話歷史
python3 chatgpt_cdp.py ask "長回答" --check 3           # 每 3 秒檢查，等回答穩定
python3 chatgpt_cdp.py type "只打字不送出"
python3 chatgpt_cdp.py history
```

## 新功能說明

### `--new` — 清除歷史對話

導航到 `/new` 頁面，建立全新的對話。適合需要乾淨起點的場景。
會自動重新關閉登入窗（如果有彈出）。

### 5. `--check <sec>` — 智慧輪詢回答

取代過去固定等待 `--wait N 秒` 的做法：

1. 送出訊息後，記錄當前 `body.innerText.length` 為基準值
2. 每 `--check` 秒輪詢一次 body 長度
3. 如果長度**成長**（與 baseline 比較）→ 回答正在產生（streaming），繼續等待
4. 如果長度**不變**連續 2 次 → 回答完成，立即返回
5. 超過 `--wait` 總秒數 → 視為超時

**⚠️ Polling 比較基準 bug（2026-05-07 修復）**：
如果比較 `body_len > prev_len`（prev_len 每次重置），會永遠為 true，stable 計數器永不觸發，導致 120 秒 timeout。
**正確做法**：比較 `body_len > baseline_len`（與初始基準比）。

優點：
- 快速回答（1-2 秒）不會浪費等待時間
- 長回答（30 秒+）也能正確等待完成
- 避免過度等待短回答，也避免截斷長回答

### 6. CDP 回應結構

### 1. 輸入框打字 — 用 JS prototype setter（不用 Input.dispatchKeyEvent）

ChatGPT 使用 React，`Input.dispatchKeyEvent` 無法正確觸發 React 狀態更新（只輸入第一個字就卡住）。
**✅ 正確方法：用 Object.getOwnPropertyDescriptor 設定 value + dispatch input/change 事件 + form.requestSubmit() 送出**

```python
# 設定值（觸發 React 更新）
ta = document.querySelector('textarea')
ns = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set
ns.call(ta, '你好')
ta.dispatchEvent(new Event('input', {bubbles: true}))
ta.dispatchEvent(new Event('change', {bubbles: true}))

# 送出（不要用 Input.dispatchMouseEvent 點按鈕、不要用 Input.dispatchKeyEvent Enter）
form = document.querySelector('form')
form.requestSubmit()  # action: https://chatgpt.com/new, method: get
```

**關鍵要點：**
- JS setter 是有效的（React 能偵測到值變化）
- 送出必須用 `form.requestSubmit()`（不是點按鈕、不是按 Enter）
- 表單 action 是 `https://chatgpt.com/new`，method 是 `GET`
- textarea name 是 `prompt-textarea`

### 2. CDP 連線後必須 drain events

ChatGPT 是 SPA 頁面，產出大量事件。連線後不 drain 會阻擋後續指令。
CDP `recv()` 回傳兩種訊息：
- **回應（有 "id"）**：你發送指令的回應
- **事件（無 "id"）**：Chrome 主動推播的事件（如 DOM 變化、網路請求）

`wait_resp()` 函數必須 loop 讀取並丟棄所有無 id 的事件，直到收到匹配的 id 回應。

### 3. 等待回覆至少 8-10 秒

ChatGPT 需要時間處理。未登入模式下建議等待 8-10 秒。

### 4. 自動關閉登入窗機制

`ask` 指令會自動偵測並關閉登入彈窗：

1. **偵測**：查找有 dark backdrop + close button + login text 的 overlay
2. **定位關閉按鈕**：透過 aria-label（close/dismiss）、文字（✕/X）、位置（top-right corner）
3. **點擊關閉**：使用 `Input.dispatchMouseEvent` 模擬點擊
4. **驗證**：再次偵測，如果還在則用 fallback（直接隱藏 overlay 元素）

> 注意：`composer-parent` div 會被排除（它有 `role="presentation"` 但不是 modal）

### 5. CDP 回應結構

```python
# recv() 回傳 {"id": N, "result": {...}, ...}
# eval_js() 回傳 result.result.value（原始值）

# 常見回應：
msg["result"]["result"]["value"]  # string, number, boolean
msg["result"]["result"]["type"]   # "string", "number", "object"
msg["error"]                       # 錯誤訊息
```

### 6. 等待回覆至少 3-5 秒

ChatGPT 需要時間處理。

## 常見問題

**Q: ask_streaming 等 120 秒才 timeout，或永遠不回？**

**根因**：polling 比較基準錯了。用 `body_len > prev_len` 會永遠為 true（因為 prev_len 每次都重置為 body_len），所以 `consecutive_stable` 永遠不遞增。

**修復**：改成 `body_len > baseline_len`，讓 stable 計數器只在 body 連續 2 次沒變化時觸發。
```python
# ❌ 錯誤：prev_len 每次都重置，永遠成立
if body_len > prev_len:
    prev_len = body_len
# ✅ 正確：跟初始 baseline 比較
if body_len > baseline_len:
    consecutive_stable = 0
else:
    consecutive_stable += 1
    if consecutive_stable >= 2 and last_text:
        yield "\n"
        return
```

**Q: FastAPI async handler 呼叫 cdp.ask() 無限期阻塞？**

**原因**：CDP WebSocket 操作是阻塞的，在 FastAPI 的 async event loop 中執行會凍結所有請求。

**修復**：用 `ThreadPoolExecutor` 在背景執行 CDP 操作，async handler 只負責收發：
```python
from concurrent.futures import ThreadPoolExecutor
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="cdp")

def _ask_sync(message: str) -> str:
    """Runs in thread pool."""
    chunks = []
    for chunk in cdp_instance.ask_streaming(message, check_interval=2, max_wait=120, start_new=True):
        if chunk != "\n":
            chunks.append(chunk)
    return "".join(chunks)

# In async handler:
loop = asyncio.get_event_loop()
full_text = await loop.run_in_executor(_executor, _ask_sync, message)
```

**替代方案**：用 `subprocess.run()` 呼叫 `scripts/chatgpt_ask.py`（完全隔離進程，但開銷較高）。

**Q: 伺服器啟動時 CDP 連線拖慢啟動？**

**修復**：改用 lazy connection — lifespan 不連線，第一次 `/v1/chat/completions` 請求時才 `init_cdp()`。

## 常見問題

**Q: 打字後按 Enter，頁面變回首頁但沒有回覆？**
A: 確定用的是 `form.requestSubmit()` 送出，不是 `Input.dispatchKeyEvent` Enter 或 `Input.dispatchMouseEvent` 點按鈕。

**Q: 訊息只輸入了一個字就卡住？**
A: 不要使用 `Input.dispatchKeyEvent` 逐字輸入（React 不追蹤）。改用 JS prototype setter：
`Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(ta, '文字')`

**Q: 連續對話失敗？**
A: 每次 `ask` 都是新連接。ChatGPT 未登入模式有使用量限制，
如果連續對話失效，請重新導航到 `https://chatgpt.com/new` 再試。

**Q: `recv()` 超時？**
A: ChatGPT 是 SPA，React 重渲染會產出大量 WS 事件。
確保連線後有足夠的 `drain_events()` 呼叫。

**Q: 登入窗關不掉？**
A: 工具會自動 fallback 到直接隱藏 overlay 元素。
如果仍然失敗，請手動關閉 Chrome 中的登入彈窗。

## 相關

- `chrome-cdp-automation` — Chrome 9222 CDP 通用自動化 skill (references/chatgpt-guest-typing.md 說明為什麼 JS setter 無效)
- `scripts/chatgpt_cdp.py` — CLI 自動化工具（可直接執行）
- `references/comparison-with-other-tools.md` — 與其他 ChatGPT 自動化工具的深度比較（soberized/ChatGPT-API-Bypass 等）
- `references/cdp-form-submission-verified.md` — CDP 表單提交正確方法 + 無效方法彙整（2026-05-07 實測）
- `references/openai-bridge-server-pattern.md` — OpenAI API 橋接伺服器架構、ThreadPoolExecutor + SSE streaming 實作模式、lazy CDP 連線
