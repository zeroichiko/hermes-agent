#!/usr/bin/env python3
"""
chatgpt_ask.py — Subprocess wrapper for chatgpt_cdp_backend.ask()

被 server.py 呼叫，用來執行 CDP ask 操作。
這避開了 async/threading 問題，因為每次都是全新的 Python 進程。
"""
import sys
import os
import time
import json

# Add skill directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chatgpt_cdp_backend import ChatGPTCDP


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Ask ChatGPT via CDP")
    parser.add_argument("message", help="Message to send")
    parser.add_argument("--check", type=int, default=2, help="Check interval (seconds)")
    parser.add_argument("--wait", type=int, default=120, help="Max wait (seconds)")
    parser.add_argument("--stream", action="store_true", help="Enable streaming output")
    args = parser.parse_args()

    cdp = ChatGPTCDP(port=9222, drain=30)
    try:
        if args.stream:
            # Streaming mode: print each chunk as it arrives
            for chunk in cdp.ask_streaming(
                message=args.message,
                check_interval=args.check,
                max_wait=args.wait,
                start_new=True,
            ):
                print(chunk, end="", flush=True)
        else:
            # Non-streaming mode: wait for full response
            response = cdp.ask(
                message=args.message,
                check_interval=args.check,
                max_wait=args.wait,
                start_new=True,
            )
            print(response, end="")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        cdp.close()


if __name__ == "__main__":
    main()
