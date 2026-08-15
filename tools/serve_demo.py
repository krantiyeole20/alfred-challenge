#!/usr/bin/env python3
"""Local demo server: static files + a streaming Gemini proxy.

The deployed demo uses the Cloudflare Worker in worker/. This is the same
contract for local use, so the agent works on your machine without a
Cloudflare account, an install, or a deploy.

It exists for one reason, same as the Worker: an API key cannot ship in a
static page. Tool calls still execute in the browser against the local SQLite
copy -- this process never sees the mailbox.

    python tools/serve_demo.py
    -> http://localhost:8899/demo/

Reads GEMINI_API_KEY from .env. Serving the page and the proxy from one origin
also means no CORS to configure.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "docs"
PORT = int(os.getenv("ALFRED_PORT", "8899"))

sys.path.insert(0, str(ROOT))
from src.pipeline import config  # noqa: E402

ALLOWED_MODELS = {"gemini-3.5-flash-lite", "gemini-3.6-flash"}
DEFAULT_MODEL = "gemini-3.5-flash-lite"
MAX_BODY = 512 * 1024
MAX_TURNS = 40


class Handler(SimpleHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        if self.path != "/api/chat":
            self.send_error(404, "POST /api/chat")
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._json(413, {"error": "request too large"})
            return

        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid JSON"})
            return

        contents = payload.get("contents")
        if not isinstance(contents, list) or not contents:
            self._json(400, {"error": "contents must be a non-empty array"})
            return
        if len(contents) > MAX_TURNS:
            self._json(400, {"error": f"conversation too long (max {MAX_TURNS} turns)"})
            return

        model = payload.get("model") or DEFAULT_MODEL
        if model not in ALLOWED_MODELS:
            self._json(400, {"error": f"model not allowed: {model}"})
            return

        key = config.GEMINI_API_KEY
        if not key:
            self._json(500, {"error": "GEMINI_API_KEY is not set in .env"})
            return

        body = {
            "contents": contents,
            "generationConfig": {"temperature": 0, "maxOutputTokens": 2048},
        }
        if payload.get("systemInstruction"):
            body["systemInstruction"] = payload["systemInstruction"]
        if payload.get("tools"):
            body["tools"] = payload["tools"]

        # alt=sse gives us Gemini's own event stream, which we relay verbatim
        # so the browser sees tokens as they are produced.
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:streamGenerateContent?alt=sse"
        )
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
            method="POST",
        )

        try:
            upstream = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            self._json(exc.code if exc.code != 429 else 429,
                       {"error": "upstream model error", "detail": detail})
            return
        except Exception as exc:  # noqa: BLE001
            self._json(502, {"error": f"cannot reach model: {exc}"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            for raw in upstream:
                self.wfile.write(raw)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the tab navigated away mid-answer

    def _json(self, status: int, obj: dict) -> None:
        blob = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def end_headers(self):
        # The SQLite file is fetched with a Range-free GET and decompressed in
        # the page; no-store keeps a rebuilt export from being served stale.
        if self.path.endswith((".db", ".db.gz", ".json")):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        if "/api/chat" in (args[0] if args else ""):
            sys.stderr.write("  → model turn\n")


def main() -> None:
    if not config.GEMINI_API_KEY:
        print("warning: GEMINI_API_KEY not set in .env — the agent will error,\n"
              "         but the six questions still work.\n", file=sys.stderr)
    handler = partial(Handler, directory=str(WEB))
    server = ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    print(f"alfred_ demo   http://localhost:{PORT}/demo/")
    print(f"design doc     http://localhost:{PORT}/")
    print("ctrl-c to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
