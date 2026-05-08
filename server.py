#!/usr/bin/env python3
"""
server.py - ChatGPT CDP to OpenAI API bridge

Uses subprocess to call chatgpt_ask.py (which wraps ChatGPTCDP.ask_streaming).
Accepts OpenAI-compatible requests on port 8080.
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
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import uvicorn

# ─── Config ──────────────────────────────────────────────────────────────────

CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "2"))
MAX_WAIT = int(os.environ.get("MAX_WAIT", "120"))
CDP_SCRIPT = os.path.join(os.path.dirname(__file__), "scripts", "chatgpt_ask.py")

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


# ─── CDP Helper (subprocess-based) ──────────────────────────────────────────


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
    description="OpenAI-compatible API for ChatGPT via Chrome CDP (subprocess backend)",
    version="1.0.0",
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
        "cdp_script": CDP_SCRIPT,
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

    log.info(f"[REQUEST] stream={request.stream} msg: {message[:60]}...")

    if request.stream:
        # SSE streaming: run CDP in thread pool, feed chunks via queue
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        request_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        now = int(time.time())

        def _stream_worker():
            """Run subprocess, push stdout chunks to queue."""
            try:
                cmd = [
                    sys.executable, CDP_SCRIPT, message,
                    "--check", str(CHECK_INTERVAL),
                    "--wait", str(120),
                ]
                log.info(f"[STREAM] Starting subprocess for streaming")
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1
                )
                for line in proc.stdout:
                    line = line.rstrip('\n')
                    if line:
                        queue.put_nowait({"chunk": line})
                proc.wait()
                if proc.returncode != 0:
                    stderr = proc.stderr.read().strip()
                    queue.put_nowait({"error": f"Subprocess failed (rc={proc.returncode}): {stderr[:200]}"})
                else:
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
    else:
        # Non-streaming: run CDP in thread pool
        loop = asyncio.get_event_loop()
        try:
            full_text = await loop.run_in_executor(None, _ask_via_script, message, CHECK_INTERVAL, 120)
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


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=SERVER_PORT)
    parser.add_argument("--cdp-port", type=int, default=CDP_PORT)
    parser.add_argument("--check-interval", type=int, default=CHECK_INTERVAL)
    args = parser.parse_args()
    
    SERVER_PORT = args.port
    CDP_PORT = args.cdp_port
    CHECK_INTERVAL = args.check_interval
    
    print(f"Starting ChatGPT-CDP Bridge on port {SERVER_PORT}")
    print(f"  CDP port: {CDP_PORT}")
    print(f"  Check interval: {CHECK_INTERVAL}s")
    print(f"  CDP script: {CDP_SCRIPT}")
    print("=" * 60)
    
    uvicorn.run(app, host="127.0.0.1", port=SERVER_PORT, log_level="info")
