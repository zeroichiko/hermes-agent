#!/usr/bin/env python3
"""
chatgpt_cdp_backend.py - ChatGPT CDP backend core module

Maintains a persistent WebSocket connection to Chrome CDP port 9222.
Supports typing, sending, waiting for replies, streaming responses.

Updated for ChatGPT new UI (ProseMirror contenteditable input, thread-content structure).
"""

import json
import time
import urllib.request
import re
import html
import websocket
import logging

log = logging.getLogger("chatgpt-cdp")


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
            ws_method = ["Page.enable", "Runtime.enable", "DOM.enable"][cmd_id - 1]
            self.ws.send(json.dumps({"id": cmd_id, "method": ws_method}))
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

    def _type_text_into_input(self, msg):
        """Type message into ChatGPT input area (ProseMirror contenteditable)."""
        # Focus the ProseMirror editor first
        self._eval_js("""
        (function() {
            // Try to find the ProseMirror contenteditable
            var editors = document.querySelectorAll('[contenteditable][role="textbox"]');
            if (editors.length > 0) {
                editors[0].focus();
                return 'focused_prosemirror';
            }
            // Fallback to textarea
            var textarea = document.querySelector('textarea');
            if (textarea) {
                textarea.focus();
                return 'focused_textarea';
            }
            return 'no_input_found';
        })()
        """)
        time.sleep(0.3)

        # Type character by character using Input events
        cmd_id = self._next_id()
        for i, ch in enumerate(msg):
            self.ws.send(json.dumps({
                "id": cmd_id + i,
                "method": "Input.dispatchKeyEvent",
                "params": {"type": "char", "text": ch}
            }))
            time.sleep(0.02)

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
        """Detect if a login modal/prompt is blocking the page."""
        result = self._eval_js("""
        (function() {
            var body = document.body.innerText;
            var result = {
                hasKeywords: false,
                bodySnippet: body.substring(0, 200),
                isLandingPage: false,
                hasModal: false,
                overlayInfo: null,
                hasInput: false
            };
            
            // Check for login/signup keywords in body
            var keywords = ['登入', 'Login', 'Sign in', '尚未登入', '免費註冊', 'Sign up', 'Log in'];
            result.hasKeywords = keywords.some(function(kw) { return body.includes(kw); });
            
            // Check if there's a visible input field (means we're on a chat page)
            var editors = document.querySelectorAll('[contenteditable][role="textbox"]');
            var textareas = document.querySelectorAll('textarea');
            var hasVisibleInput = false;
            
            editors.forEach(function(el) {
                var style = window.getComputedStyle(el);
                if (style.display !== 'none' && style.visibility !== 'hidden') {
                    hasVisibleInput = true;
                }
            });
            textareas.forEach(function(el) {
                if (el.offsetParent !== null) {
                    hasVisibleInput = true;
                }
            });
            result.hasInput = hasVisibleInput;
            
            // Check for actual blocking modals/dialogs (not the composer/popover)
            var nonModalClasses = [
                'composer-parent', 'flex flex-1', 'flex flex-col',
                'thread-list', 'sidebar', 'max-w-full', 'max-w-3xl',
                'max-w-4xl', 'sticky bottom-0', 'thread-content-max-width', 'thread-content',
                'popover', 'ProseMirror', 'ProseMirror-hint'
            ];
            
            var dialogSelectors = [
                '[role="dialog"]',
                '[role="presentation"][class*="modal"]',
                '[role="presentation"][class*="overlay"]',
                '[role="presentation"][class*="backdrop"]',
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
                    result.overlayInfo = {
                        tag: el.tagName,
                        role: el.getAttribute('role'),
                        classes: cls.substring(0, 100),
                        coverage: Math.round(coverage * 100) + '%'
                    };
                    break;
                }
            }
            
            // If we have a visible input but no modal, we're on a chat page
            if (result.hasInput && !result.hasModal) result.isLandingPage = false;
            
            return result;
        })()
        """)
        return result if isinstance(result, dict) else {}

    def _close_login_modal_auto(self):
        """Auto-detect and close login modal."""
        detection = self._is_login_modal_present()
        if not detection:
            return
        
        # If hasInput is true and no modal, we're on a chat page - no action needed
        if detection.get('hasInput', False) and not detection.get('hasModal', False):
            return
        
        # If there's a real modal, hide it
        if detection.get('hasModal', False):
            self._eval_js("""
            (function() {
                var overlays = document.querySelectorAll('[role="dialog"], [role="presentation"]');
                var hidden = 0;
                overlays.forEach(function(el) {
                    var cls = (typeof el.className === 'string') ? el.className : '';
                    var nonModalClasses = ['composer-parent', 'flex flex-1', 'flex flex-col', 'thread-list', 'sidebar', 'max-w-full', 'max-w-3xl', 'max-w-4xl', 'sticky bottom-0', 'thread-content-max-width', 'thread-content', 'popover', 'ProseMirror'];
                    if (nonModalClasses.some(function(nc) { return cls.indexOf(nc) !== -1; })) return;
                    el.style.display = 'none';
                    hidden++;
                });
                return hidden;
            })()
            """)
            time.sleep(0.3)
            self._drain_events(10)

    def _get_chat_text(self):
        """Get text content from the chat thread area - deduplicated."""
        return self._eval_js("""
        (function() {
            // Get text from the last message element in the thread
            var messageElements = document.querySelectorAll('[class*="message"][class*="text-message"]');
            if (messageElements.length === 0) {
                // Fallback to the last element with [class*="max-w-"] that has text
                var maxWElements = document.querySelectorAll('[class*="max-w-"]');
                for (var i = maxWElements.length - 1; i >= 0; i--) {
                    var el = maxWElements[i];
                    var style = window.getComputedStyle(el);
                    if (style.display !== 'none' && style.visibility !== 'hidden') {
                        var text = el.innerText;
                        if (text && text.length > 10) {
                            return text;
                        }
                    }
                }
                return '';
            }
            // Use the last message element (AI response)
            return messageElements[messageElements.length - 1].innerText;
        })()
        """, timeout=5) or ""

    def _extract_response_text(self, chat_text, user_message):
        """Extract ChatGPT response from chat text.

        chat_text structure (new UI):
          [User message]
          [AI response]

        Strategy:
        1. Find the user message in the chat text
        2. Everything after it is the AI response
        """
        if not chat_text or not user_message:
            return ""

        # Find the user message
        user_msg_idx = chat_text.find(user_message)
        if user_msg_idx < 0:
            # Fallback: return the entire chat text (it's likely just the response)
            return chat_text.strip()

        # Extract everything after the user message
        after_msg = chat_text[user_msg_idx + len(user_message):].strip()
        
        # Remove footer
        footer_patterns = [
            r'\n語音\s*$', r'\n語音$',
            r'\nChatGPT 可能會出錯。',
            r'\n請查核重要資訊',
            r'\n請前往 Cookie',
        ]
        for footer_pat in footer_patterns:
            after_msg = re.sub(footer_pat, '', after_msg)
        
        after_msg = after_msg.strip()
        if after_msg:
            return after_msg
        
        return ""

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
        
        # Type into the input area (ProseMirror contenteditable)
        self._type_text_into_input(message)
        time.sleep(0.5)
        
        # Send by pressing Enter
        self._send_enter()
        self._drain_events(10)

        # Record baseline chat text right after sending, before AI answers
        baseline_text = self._get_chat_text() or ""
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
            chat_text = self._get_chat_text() or ""
            chat_len = len(chat_text)

            # Chat text grew compared to previous poll = AI is still streaming
            if chat_len > prev_len:
                consecutive_stable = 0
                consecutive_no_growth = 0
                prev_len = chat_len
                # Extract and yield new text
                current_text = self._extract_response_text(chat_text, message)
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
                # Chat text hasn't changed since last poll
                consecutive_stable += 1
                consecutive_no_growth += 1

            # Response is stable when unchanged for 2 consecutive polls (and we have content)
            if consecutive_stable >= 2 and last_text:
                yield "\n"
                self._capture_response(last_text)
                return

            # Safety: if chat text hasn't grown after N consecutive polls, give up
            if consecutive_no_growth >= 3 and poll > 0:
                log.warning(f"[ask_streaming] No chat growth after {consecutive_no_growth} polls, giving up. chat_len={chat_len}, baseline={baseline_len}")
                # Return whatever text is after the baseline
                if baseline_text and chat_text:
                    resp = self._extract_response_text(chat_text, message)
                    if resp:
                        yield resp
                yield "\n"
                return

        # Hard timeout - yield whatever we have
        if last_text:
            yield "\n"
            self._capture_response(last_text)
        elif baseline_text and chat_text:
            resp = self._extract_response_text(chat_text, message)
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
