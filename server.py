#!/usr/bin/env python3
"""
server.py - ChatGPT CDP to OpenAI API bridge

支援兩種模式:
- 模式 A (預設): subprocess 呼叫 chatgpt_ask.py (穩定，適合生產)
- 模式 B: 直接連線 CDP WebSocket (高效，支援流式)

切換方式:
  CDP_BACKEND=process python3 server.py  # 模式 A (舊版)
  CDP_BACKEND=direct python3 server.py   # 模式 B (新版，需 websocket-client)
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse, Response
from pydantic import BaseModel, Field
import uvicorn

# ─── Config ──────────────────────────────────────────────────────────────────

CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))
CDP_BACKEND = os.environ.get("CDP_BACKEND", "process")  # process | direct
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "2"))
MAX_WAIT = int(os.environ.get("MAX_WAIT", "120"))
CDP_SCRIPT = os.path.join(os.path.dirname(__file__), "scripts", "chatgpt_ask.py")
WEBUI_DIR = os.path.join(os.path.dirname(__file__), "server_public")

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
log = logging.getLogger("chatgpt-cdp")

# ─── Pydantic Models (OpenAI API compatible) ─────────────────────────────────


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "chatgpt-cdp"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None


# ─── Mode A: Subprocess-based (Old/Compatible) ──────────────────────────────


def _ask_via_script(message: str, check_interval: int = 2, max_wait: int = 120) -> str:
    """Ask ChatGPT via subprocess calling chatgpt_ask.py."""
    cmd = [
        sys.executable, CDP_SCRIPT, message,
        "--check", str(check_interval),
        "--wait", str(max_wait),
    ]
    log.info(f"[SUBPROCESS] message={message[:60]}...")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=max_wait + 30
        )
        stderr = result.stderr.strip() if result.stderr else ""
        stdout = result.stdout.strip() if result.stdout else ""
        log.info(f"[SUBPROCESS] rc={result.returncode} stdout={len(stdout)} chars stderr={len(stderr)} chars")
        if stderr:
            log.warning(f"[SUBPROCESS] stderr: {stderr[:200]}")
        if result.returncode != 0 and not stdout:
            raise RuntimeError(f"chatgpt_ask.py failed (rc={result.returncode}): {stderr[:200]}")
        return stdout
    except subprocess.TimeoutExpired:
        log.error(f"[SUBPROCESS] TIMEOUT after {max_wait + 30}s for: {message[:60]}...")
        raise RuntimeError(f"ChatGPT timed out after {max_wait}s")
    except Exception as e:
        log.error(f"[SUBPROCESS] Error: {e}")
        raise


# ─── Mode B: Direct CDP WebSocket (New/Performance) ─────────────────────────

_direct_cdp = None
_direct_lock = threading.Lock()


def _get_direct_cdp():
    """Lazy initialization of direct CDP connection."""
    global _direct_cdp
    if _direct_cdp is None:
        try:
            from chatgpt_cdp_backend import ChatGPTCDP
            with _direct_lock:
                if _direct_cdp is None:
                    _direct_cdp = ChatGPTCDP(port=CDP_PORT, drain=30)
            log.info("[DIRECT] CDP WebSocket connected")
        except Exception as e:
            log.error(f"[DIRECT] Failed to connect: {e}")
            raise
    return _direct_cdp


def _ask_direct(message: str, check_interval: int = 2, max_wait: int = 120) -> str:
    """Ask ChatGPT via direct CDP WebSocket."""
    cdp = _get_direct_cdp()
    try:
        return cdp.ask(
            message=message,
            check_interval=check_interval,
            max_wait=max_wait,
            start_new=True,
        )
    except Exception as e:
        log.error(f"[DIRECT] Error: {e}")
        raise


# ─── SSE Helpers ─────────────────────────────────────────────────────────────


def _sse_line(request_id, chunk_id, now, delta_content=None, finish_reason=None):
    """Build one SSE data line."""
    delta = {}
    if delta_content is not None:
        delta["content"] = delta_content
    data = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": now,
        "model": "chatgpt-cdp",
        "choices": [{
            "index": 0,
            "delta": delta,
            **({"finish_reason": finish_reason} if finish_reason else {}),
        }]
    }
    return f"id: {request_id}\nevent: message\ndata: {json.dumps(data)}\n\n"


# ─── FastAPI App ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="ChatGPT-CDP Bridge",
    description="OpenAI-compatible API for ChatGPT via Chrome CDP",
    version="2.0.0",
)


@app.middleware("http")
async def log_requests(request, call_next):
    start = time.time()
    response = await call_next(request)
    log.info(f"{request.method} {request.url.path} -> {response.status_code} ({time.time()-start:.2f}s)")
    return response


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "server": "running",
        "port": SERVER_PORT,
        "cdp_backend": CDP_BACKEND,
        "cdp_port": CDP_PORT,
        "cdp_script_exists": os.path.exists(CDP_SCRIPT),
    }


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [{
            "id": "chatgpt-cdp",
            "object": "model",
            "created": int(time.time()),
            "owned_by": "chatgpt-cdp",
        }]
    }


@app.post("/v1/chat/completions")
async def create_chat_completion(request: ChatCompletionRequest):
    """Main endpoint - ChatGPT question/answer via OpenAI API."""
    # Get the last user message
    user_messages = [m for m in request.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="No user message found")
    message = user_messages[-1].content

    log.info(f"[REQUEST] backend={CDP_BACKEND} stream={request.stream} msg: {message[:60]}...")

    if request.stream:
        return _handle_streaming(message, request)
    else:
        return _handle_nonstreaming(message, request)


def _handle_streaming(message, request):
    """Handle streaming response based on backend mode."""
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()
    request_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    now = int(time.time())

    def _stream_worker():
        """Run CDP ask, push chunks to queue."""
        try:
            if CDP_BACKEND == "direct":
                # Direct CDP mode
                cdp = _get_direct_cdp()
                for chunk in cdp.ask_streaming(
                    message=message,
                    check_interval=CHECK_INTERVAL,
                    max_wait=MAX_WAIT,
                    start_new=True,
                ):
                    queue.put_nowait({"chunk": chunk})
            else:
                # Subprocess mode (legacy)
                cmd = [
                    sys.executable, CDP_SCRIPT, message,
                    "--check", str(CHECK_INTERVAL),
                    "--wait", str(MAX_WAIT),
                ]
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1
                )
                for line in proc.stdout:
                    line = line.rstrip('\n')
                    if line:
                        queue.put_nowait({"chunk": line})
                proc.wait()

            queue.put_nowait({"done": True})
        except Exception as e:
            log.error(f"[STREAM] Error: {e}")
            queue.put_nowait({"error": str(e)})

    loop.run_in_executor(None, _stream_worker)

    async def gen():
        while True:
            item = await queue.get()
            if "error" in item:
                yield _sse_line(request_id, "chatcmpl-err", now,
                                delta_content=f"[Error] {item['error']}",
                                finish_reason="error")
                break
            if "done" in item:
                yield _sse_line(request_id, "chatcmpl-done", now,
                                finish_reason="stop")
                break
            chunk_text = item.get("chunk", "")
            if chunk_text:
                chunk_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
                yield _sse_line(request_id, chunk_id, now,
                                delta_content=chunk_text)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


def _handle_nonstreaming(message, request):
    """Handle non-streaming response based on backend mode."""
    loop = asyncio.get_event_loop()
    try:
        if CDP_BACKEND == "direct":
            full_text = loop.run_in_executor(None, _ask_direct, message, CHECK_INTERVAL, MAX_WAIT)
        else:
            full_text = loop.run_in_executor(None, _ask_via_script, message, CHECK_INTERVAL, MAX_WAIT)
    except Exception as e:
        log.error(f"CDP error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    response_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    return {
        "id": response_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "chatgpt-cdp",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": full_text},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": len(message),
            "completion_tokens": len(full_text),
            "total_tokens": len(message) + len(full_text),
        },
    }


# ─── Web UI Endpoints (llama.cpp style) ──────────────────────────────────────

def _load_webui_files():
    """Load web UI files if available. Returns (index_html, app_js, style_css, bundle_js, bundle_css, loading_html).
    bundle_js/bundle_css are optional fallbacks — missing files return None for those slots only."""
    if not os.path.exists(WEBUI_DIR):
        return None, None, None, None, None, None

    def _read_file(name):
        path = os.path.join(WEBUI_DIR, name)
        if os.path.exists(path):
            return open(path, "rb").read()
        return None

    index_html = _read_file("index.html")
    app_js = _read_file("app.js")
    style_css = _read_file("style.css")
    bundle_js = _read_file("bundle.js")
    bundle_css = _read_file("bundle.css")
    loading_html = _read_file("loading.html")
    return index_html, app_js, style_css, bundle_js, bundle_css, loading_html


@app.get("/", response_class=HTMLResponse)
async def web_ui_index():
    """Serve the llama.cpp-style chat UI."""
    index_html, _, _, _, _, _ = _load_webui_files()
    if index_html:
        return HTMLResponse(
            content=index_html.decode("utf-8"),
            headers={
                "Cross-Origin-Embedder-Policy": "require-corp",
                "Cross-Origin-Opener-Policy": "same-origin",
            },
        )
    return HTMLResponse(
        content="<html><body><h1>Web UI not available</h1><p>Place index.html, bundle.js, bundle.css in server_public/</p></body></html>",
        status_code=503,
    )


@app.get("/index.html")
async def web_ui_index_html():
    """Serve index.html."""
    index_html, _, _, _, _, _ = _load_webui_files()
    if index_html:
        return Response(content=index_html, media_type="text/html; charset=utf-8")
    return Response(status_code=503)


@app.get("/app.js")
async def web_ui_app_js():
    """Serve app.js (custom frontend for ChatGPT-CDP)."""
    _, app_js, _, _, _, _ = _load_webui_files()
    if app_js:
        return Response(content=app_js, media_type="application/javascript; charset=utf-8")
    return Response(status_code=503)


@app.get("/style.css")
async def web_ui_style_css():
    """Serve style.css."""
    _, _, style_css, _, _, _ = _load_webui_files()
    if style_css:
        return Response(content=style_css, media_type="text/css; charset=utf-8")
    return Response(status_code=503)


@app.get("/bundle.js")
async def web_ui_bundle_js():
    """Serve bundle.js (compiled Svelte frontend, fallback)."""
    _, _, _, bundle_js, _, _ = _load_webui_files()
    if bundle_js:
        return Response(content=bundle_js, media_type="application/javascript; charset=utf-8")
    return Response(status_code=503)


@app.get("/bundle.css")
async def web_ui_bundle_css():
    """Serve bundle.css (fallback)."""
    _, _, _, _, bundle_css, _ = _load_webui_files()
    if bundle_css:
        return Response(content=bundle_css, media_type="text/css; charset=utf-8")
    return Response(status_code=503)


@app.get("/loading.html")
async def web_ui_loading():
    """Serve loading indicator."""
    _, _, _, _, _, loading_html = _load_webui_files()
    if loading_html:
        return HTMLResponse(content=loading_html.decode("utf-8"))
    return HTMLResponse("<html><body>Loading...</body></html>")


# ─── Startup/Shutdown ────────────────────────────────────────────────────────

@app.on_event("shutdown")
async def shutdown():
    """Cleanup CDP connection on shutdown."""
    global _direct_cdp
    if _direct_cdp:
        try:
            _direct_cdp.close()
        except Exception:
            pass
        _direct_cdp = None


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=SERVER_PORT)
    parser.add_argument("--cdp-port", type=int, default=CDP_PORT)
    parser.add_argument("--backend", choices=["process", "direct"], default=CDP_BACKEND)
    parser.add_argument("--check-interval", type=int, default=CHECK_INTERVAL)
    args = parser.parse_args()

    SERVER_PORT = args.port
    CDP_PORT = args.cdp_port
    CDP_BACKEND = args.backend
    CHECK_INTERVAL = args.check_interval

    print(f"\n{'='*60}")
    print(f"  ChatGPT-CDP Bridge v2.0")
    print(f"{'='*60}")
    print(f"  Backend    : {CDP_BACKEND}")
    print(f"  LLM API    : http://0.0.0.0:{SERVER_PORT}")
    print(f"  CDP Port   : {CDP_PORT}")
    print(f"  Check Int  : {CHECK_INTERVAL}s")
    print(f"{'='*60}\n")

    uvicorn.run(app, host="0.0.0.0", port=SERVER_PORT, log_level="info")
