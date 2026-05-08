# OpenAI API Bridge Server Pattern (2026-05-07)

## Architecture

ChatGPT CDP → OpenAI API compatible bridge using FastAPI + ThreadPoolExecutor.

```
External Tool (LM Studio / custom client)
        │  POST /v1/chat/completions (OpenAI format)
        ▼
┌─────────────────────┐
│  FastAPI Server     │  port 8080
│                     │
│  GET  /health       │  quick status (no CDP)
│  GET  /v1/models    │  list available models
│  POST /v1/chat/completions │ main endpoint
│    - stream=false   │ returns JSON
│    - stream=true    │ returns SSE chunks
│                     │
│  Lazy CDP init:     │
│  ThreadPoolExecutor │  runs blocking CDP ops
│                     │
└──────────┬──────────┘
           │  _ask_sync() / _ask_stream_sync()
           ▼
┌─────────────────────┐
│  ChatGPTCDP         │  persistent WebSocket
│                     │
│  ask_streaming()    │  poll body length
│  _navigate_to_new() │
│  _close_login_modal │
└──────────┬──────────┘
           │  Chrome DevTools Protocol
           ▼
┌─────────────────────┐
│  Chrome 9222        │
│  ChatGPT tab        │
└─────────────────────┘
```

## Key Implementation Details

### 1. Lazy CDP Connection

CDP connection is blocking (finds tab, establishes WebSocket). Do NOT init in lifespan — connect on first request:

```python
cdp_ready = False

@app.post("/v1/chat/completions")
async def create_chat_completion(request):
    if not cdp_ready:
        init_cdp()  # blocking, but in background thread
    # ... proceed
```

### 2. ThreadPoolExecutor for Blocking CDP Ops

CDP WebSocket recv/send is synchronous and blocks. In async handler:

```python
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="cdp")

def _ask_sync(message: str) -> str:
    return "".join(
        chunk for chunk in cdp_instance.ask_streaming(
            message, check_interval=2, max_wait=120, start_new=True
        ) if chunk != "\n"
    )

# In handler:
full_text = await loop.run_in_executor(_executor, _ask_sync, message)
```

For SSE streaming, use a Queue to push chunks from thread to async:

```python
def _ask_stream_sync(message: str, queue: asyncio.Queue):
    for chunk in cdp_instance.ask_streaming(message, ...):
        queue.put_nowait({"chunk": chunk})
    queue.put_nowait({"done": True})

# In handler:
loop.run_in_executor(_executor, _ask_stream_sync, message, queue)
while True:
    item = await queue.get()
    if "done" in item or "error" in item: break
    yield f"data: {json.dumps(...)}\n\n"
```

### 3. SSE Format

```
id: chatcmpl-xxxx
event: message
data: {"id":"chatcmpl-xxxx","object":"chat.completion.chunk","created":1234,"model":"chatgpt-cdp","choices":[{"index":0,"delta":{"content":"Hello"}}]}

```

Final chunk uses `delta: {}` and `finish_reason: "stop"`.

### 4. Performance

- **Before fix**: 120s timeout due to polling bug
- **After fix**: ~10s average response time
- Critical fix: `body_len > baseline_len` (not `prev_len`)

### 5. File Locations

- `server.py` — FastAPI bridge server
- `chatgpt_cdp_backend.py` — CDP controller (ask_streaming fixed)
- `scripts/chatgpt_ask.py` — subprocess wrapper (fallback)
- `DESIGN.md` — Full architecture design

### 6. Subprocess Approach (Fallback)

If ThreadPoolExecutor fails (e.g., Python version incompatibility), use subprocess:

```python
result = subprocess.run(
    [sys.executable, "scripts/chatgpt_ask.py", message,
     "--check", "2", "--wait", "120"],
    capture_output=True, text=True, timeout=130
)
return result.stdout
```

Trade-off: ~50ms Python startup per request vs thread overhead. Acceptable for low-traffic use.