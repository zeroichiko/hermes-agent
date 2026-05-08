#!/usr/bin/env python3
"""
chatgpt_cdp_backend.py - ChatGPT CDP backend core module

Maintains a persistent WebSocket connection to Chrome CDP port 9222.
Supports typing, sending, waiting for replies, streaming responses.
"""

import json
import time
import urllib.request
import re
import html
import websocket


class ChatGPTCDP:
    """ChatGPT unauthenticated-mode CDP controller.

    Usage:
        cdp = ChatGPTCDP(port=9222)
        for chunk in cdp.ask_stream("hello"):
            print(chunk, end="", flush=True)
        cdp.close()
    """

    def __init__(self, port=9222, url="https://chatgpt.com/", drain=50):
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.ws = None
        self.tab = None
        self._cmd_counter = 0
        self._connect(drain)

    def _next_id(self):
        self._cmd_counter += 1
        return self._cmd_counter

    def _connect(self, drain=50):
        tabs = self._get_tabs()
        if not tabs:
            raise RuntimeError(
                f"No Chrome tabs found. Is Chrome running with "
                f"--remote-debugging-port={self.port}?"
            )
        self.tab = self._find_chatgpt_tab(tabs)
        if not self.tab:
            raise RuntimeError("No ChatGPT tab found. Please open ChatGPT in Chrome.")
        self.ws = websocket.create_connection(self.tab["webSocketDebuggerUrl"])
        # Enable CDP domains
        for cmd_id in [1, 2, 3]:
            self.ws.send(json.dumps({"id": cmd_id, "method": [
                "Page.enable", "Runtime.enable", "DOM.enable"][cmd_id - 1]}))
        self._drain_events(drain)

    def _get_tabs(self):
        with urllib.request.urlopen(f"{self.base}/json") as r:
            return json.loads(r.read())

    def _find_chatgpt_tab(self, tabs):
        for t in tabs:
            if 'chatgpt' in t.get('url', '').lower():
                return t
        return tabs[0] if tabs else None

    def _send_cmd(self, method, params=None):
        cmd_id = self._next_id()
        self.ws.send(json.dumps({"id": cmd_id, "method": method, "params": params or {}}))
        return cmd_id

    def _recv_resp(self, cmd_id, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            self.ws.settimeout(min(0.5, remaining))
            try:
                msg = json.loads(self.ws.recv())
                if msg.get("id") == cmd_id:
                    return msg
            except Exception:
                pass
        return None

    def _recv_result(self, cmd_id, timeout=5):
        msg = self._recv_resp(cmd_id, timeout)
        return msg.get("result", {}) if msg else None

    def _eval_js(self, expr, timeout=10):
        """Execute JavaScript, return result value."""
        cmd_id = self._send_cmd("Runtime.evaluate", {
            "expression": expr, "returnByValue": True
        })
        result = self._recv_result(cmd_id, timeout)
        if not result:
            return None
        inner = result.get("result", {})
        if not isinstance(inner, dict):
            return result
        val = inner.get("value")
        if val is not None:
            return val
        desc = inner.get("description", "")
        return desc if desc else result

    def _drain_events(self, count=5):
        for _ in range(count):
            self.ws.settimeout(0.2)
            try:
                json.loads(self.ws.recv())
            except Exception:
                break

    def _navigate_to_new(self):
        """Navigate to /new to start a fresh conversation."""
        self._send_cmd("Runtime.evaluate", {
            "expression": "location.href = '/new'",
            "awaitPromise": True,
            "returnByValue": True
        })
        self._recv_resp(self._cmd_counter, 15)
        time.sleep(1)
        self._drain_events(20)

    def _type_char_by_char(self, msg, start_id=None):
        """Type message character by character into textarea."""
        if start_id is None:
            start_id = self._next_id()
        for i, ch in enumerate(msg):
            self.ws.send(json.dumps({
                "id": start_id + i,
                "method": "Input.dispatchKeyEvent",
                "params": {"type": "char", "text": ch}
            }))
            time.sleep(0.03)

    def _send_enter(self):
        """Press Enter to send message."""
        self._send_cmd("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": "Enter", "code": "Enter", "keyCode": 13
        })
        self._send_cmd("Input.dispatchKeyEvent", {
            "type": "char", "text": "\n", "key": "Enter", "code": "Enter"
        })
        self._send_cmd("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "Enter", "code": "Enter", "keyCode": 13
        })

    def _is_login_modal_present(self):
        """Detect if login modal is present."""
        result = self._eval_js("""
        (function() {
            var body = document.body.innerText;
            var result = {
                hasKeywords: false, bodySnippet: body.substring(0, 200),
                isLandingPage: false, hasModal: false, overlayInfo: null,
                textareaVisible: false
            };
            var keywords = ['登入', 'Login', 'Sign in', '尚未登入', '免費註冊', 'Sign up'];
            result.hasKeywords = keywords.some(function(kw) { return body.includes(kw); });
            var textarea = document.querySelector('textarea');
            if (textarea && textarea.offsetParent !== null) result.textareaVisible = true;
            var nonModalClasses = [
                'composer-parent', 'flex flex-1', 'flex flex-col',
                'thread-list', 'sidebar', 'max-w-full', 'max-w-3xl',
                'max-w-4xl', 'sticky bottom-0', 'thread-content-max-width', 'thread-content',
            ];
            var dialogSelectors = [
                '[role="dialog"]',
                '[role="presentation"][class*="modal"]',
                '[role="presentation"][class*="overlay"]',
                '[role="presentation"][class*="backdrop"]',
                '[role="presentation"][class*="popup"]',
                '[role="presentation"][class*="signin"]',
                '[role="presentation"][class*="login"]',
            ];
            var overlays = document.querySelectorAll(dialogSelectors.join(', '));
            for (var i = 0; i < overlays.length; i++) {
                var el = overlays[i];
                var style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
                var cls = (typeof el.className === 'string') ? el.className : '';
                if (nonModalClasses.some(function(nc) { return cls.indexOf(nc) !== -1; })) continue;
                var rect = el.getBoundingClientRect();
                var coverage = (rect.width * rect.height) / (window.innerWidth * window.innerHeight);
                if (coverage > 0.3) {
                    result.hasModal = true;
                    result.overlayInfo = { tag: el.tagName, role: el.getAttribute('role'),
                        classes: cls.substring(0, 100), coverage: Math.round(coverage * 100) + '%' };
                    break;
                }
            }
            if (result.textareaVisible && !result.hasModal) result.isLandingPage = true;
            return result;
        })()
        """)
        return result if isinstance(result, dict) else {}

    def _close_login_modal_auto(self):
        """Auto-detect and close login modal."""
        detection = self._is_login_modal_present()
        if not detection:
            return
        if detection.get('isLandingPage', False) and not detection.get('hasModal', False):
            return
        if not detection.get('hasModal', False):
            return

        # Hide modal elements
        self._eval_js("""
        (function() {
            var overlays = document.querySelectorAll('[role="dialog"], [role="presentation"], dialog, .modal, .overlay, [class*="modal"], [class*="overlay"], [class*="popup"]');
            overlays.forEach(function(el) { el.style.display = 'none'; });
            return overlays.length;
        })()
        """)
        time.sleep(0.3)
        self._drain_events(10)

    def _extract_response_text(self, body_text, baseline_text=""):
        """Extract ChatGPT response from body text.

        body.innerText structure:
          [Header UI: 跳至內容, ChatGPT, 登入, 免費註冊]
          [Previous conversation / user messages]
          [AI response (or error)]
          [Footer: 語音, ChatGPT 可能會出錯...]

        Strategy:
        1. Remove header UI
        2. Find where the NEW user message was typed
        3. Everything after it (minus footer) is the response
        """
        if not body_text:
            return ""

        # Step 1: Remove header UI text
        header_patterns = [
            r'^跳至內容\n', r'^跳至內容\s*\n',
            r'^ChatGPT\n', r'^ChatGPT\s*\n',
            r'^登入\n', r'^登入\s*\n',
            r'^免費註冊\n', r'^免費註冊\s*\n',
        ]
        result = body_text
        for pattern in header_patterns:
            result = re.sub(pattern, '', result, count=1)

        # Step 2: If baseline was provided, the NEW text after baseline is the response.
        # baseline_text = body text right after typing (before AI answers)
        # body_text = body text now (after AI has answered)
        # The diff = baseline_text + newline + AI_response
        if baseline_text and len(body_text) > len(baseline_text):
            # Find baseline_text in body_text
            idx = body_text.find(baseline_text)
            if idx >= 0:
                raw_response = body_text[idx + len(baseline_text):].strip()
                # Remove footer
                footer_patterns = [
                    r'\n語音\s*$', r'\n語音$',
                    r'\nChatGPT 可能會出錯。\s*$', r'\nChatGPT 可能會出錯。$',
                    r'\n請查核重要資訊', r'\n請前往 Cookie',
                    r'\n取得為你度身設計的回應', r'\n登入即可取得',
                ]
                for footer_pat in footer_patterns:
                    raw_response = re.sub(footer_pat, '', raw_response)
                raw_response = raw_response.strip()
                if raw_response:
                    return raw_response

        # Fallback: line-based extraction
        lines = result.split('\n')
        user_msg_index = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and stripped not in ('語音', 'ChatGPT 可能會出錯。'):
                user_msg_index = i
                break
        ai_response_lines = lines[user_msg_index + 1:]

        footer_patterns = [
            r'^語音\s*$', r'^語音\n',
            r'^ChatGPT 可能會出錯。', r'^請查核重要資訊',
            r'^請前往 Cookie', r'取得為你度身設計的回應', r'登入即可取得',
        ]
        footer_idx = len(ai_response_lines)
        for i, line in enumerate(ai_response_lines):
            for footer_pat in footer_patterns:
                if re.search(footer_pat, line.strip()):
                    footer_idx = i
                    break
            if footer_idx != len(ai_response_lines):
                break
        ai_response_lines = ai_response_lines[:footer_idx]

        cleaned = []
        prev_blank = False
        for line in ai_response_lines:
            stripped = line.strip()
            if stripped:
                cleaned.append(stripped)
                prev_blank = False
            elif not prev_blank and cleaned:
                cleaned.append('')
                prev_blank = True
        result = '\n'.join(cleaned).strip()
        result = re.sub(r'\n{3,}', '\n\n', result).strip()
        return result

    def ask(self, message, check_interval=2, max_wait=120,
            start_new=True, strip_html=True, max_output_len=5000):
        """Send message and get full reply.

        For streaming, use ask_streaming() instead.
        """
        for chunk in self.ask_streaming(
            message, check_interval=check_interval, max_wait=max_wait,
            start_new=start_new
        ):
            pass  # consume streaming to completion
        # Return last accumulated response via a different path
        return self._get_last_response()

    def _get_last_response(self):
        """Return the last captured response text."""
        if hasattr(self, '_last_response'):
            return self._last_response
        return ""

    def _capture_response(self, text):
        """Store captured response for non-streaming caller."""
        self._last_response = text

    def ask_streaming(self, message, check_interval=2, max_wait=120,
                      start_new=True):
        """Send message and yield response chunks as they arrive.

        Yields:
            str: Text chunks as they are detected (streaming).
            On completion: yields "\n" as final chunk.
        """
        # Steps 1-5: navigate, close modal, type, send
        if start_new:
            self._navigate_to_new()
            time.sleep(0.5)
        self._close_login_modal_auto()
        time.sleep(0.5)
        self._type_char_by_char(message)
        time.sleep(0.5)
        self._send_enter()
        self._drain_events(10)

        # Record baseline (body text right after sending, before AI answers)
        baseline_text = self._eval_js("document.body.innerText", timeout=5) or ""
        baseline_len = len(baseline_text)

        last_text = ""
        prev_len = baseline_len
        consecutive_stable = 0
        consecutive_no_growth = 0
        start_time = time.time()
        max_polls = max_wait // check_interval  # Safety: max polling iterations

        for poll in range(max_polls + 1):
            if poll > 0:
                time.sleep(check_interval)
            body_text = self._eval_js("document.body.innerText", timeout=5) or ""
            body_len = len(body_text)

            # Body grew compared to previous poll = AI is still streaming
            if body_len > prev_len:
                consecutive_stable = 0
                consecutive_no_growth = 0
                prev_len = body_len
                # Extract and yield new text
                current_text = self._extract_response_text(body_text, baseline_text)
                if current_text and current_text != last_text:
                    # Yield the delta
                    if last_text and current_text.startswith(last_text):
                        delta = current_text[len(last_text):]
                    else:
                        delta = current_text
                    if delta:
                        yield delta
                    last_text = current_text
            else:
                # Body hasn't changed since last poll
                consecutive_stable += 1
                consecutive_no_growth += 1

            # Response is stable when unchanged for 2 consecutive polls (and we have content)
            if consecutive_stable >= 2 and last_text:
                yield "\n"
                self._capture_response(last_text)
                return

            # Safety: if body hasn't grown after N consecutive polls, give up
            # This handles error pages / unauthenticated mode gracefully
            if consecutive_no_growth >= 3 and poll > 0:
                log = __import__('logging').getLogger("chatgpt-cdp")
                log.warning(f"[ask_streaming] No body growth after {consecutive_no_growth} polls, giving up. body_len={body_len}, baseline={baseline_len}")
                # Return whatever text is after the baseline
                if baseline_text and body_text:
                    idx = body_text.find(baseline_text)
                    if idx >= 0:
                        resp = body_text[idx + len(baseline_text):].strip()
                        # Remove footer noise
                        footer_patterns = [
                            r'\n語音\s*$', r'\nChatGPT 可能會出錯。',
                            r'\n請查核重要資訊', r'\n取得為你度身設計的回應',
                        ]
                        for fp in footer_patterns:
                            resp = re.sub(fp, '', resp)
                        resp = resp.strip()
                        if resp:
                            yield resp
                yield "\n"
                return

        # Hard timeout - yield whatever we have
        if last_text:
            yield "\n"
            self._capture_response(last_text)
        elif baseline_text and body_text:
            idx = body_text.find(baseline_text)
            if idx >= 0:
                resp = body_text[idx + len(baseline_text):].strip()
                if resp:
                    yield resp
                    yield "\n"

    def status(self):
        """Get current page status."""
        title = self._eval_js("document.title", timeout=3) or ""
        url = self._eval_js("location.href", timeout=3) or ""
        ready = self._eval_js("document.readyState", timeout=3) or ""
        body_preview = (self._eval_js("document.body.innerText", timeout=3) or "")[:200]
        has_modal = bool(self._is_login_modal_present())
        return {
            "title": title,
            "url": url,
            "readyState": ready,
            "body_preview": body_preview,
            "hasModal": has_modal,
        }

    def close(self):
        """Close WebSocket connection."""
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None
