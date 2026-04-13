"""Web UI gateway — serves Svelte frontend + API endpoints."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import urlparse

from gateway.base import Gateway, InboundMessage, MessageHandler, OutboundMessage

# Locate the built Svelte frontend
_WEB_DIST = Path(__file__).parent.parent / "web" / "dist"


class WebGateway(Gateway):
    """Serves the Svelte chat UI and handles API requests.

    Endpoints:
      GET  /             — Svelte app (index.html)
      GET  /assets/*     — Static assets (JS, CSS)
      POST /api/chat     — Send message, get response
      GET  /api/skills   — List loaded skills
    """

    def __init__(self, port: int = 8080, host: str = "0.0.0.0") -> None:
        self.port = port
        self.host = host
        self._handler: MessageHandler | None = None
        self._server: HTTPServer | None = None
        self._thread: Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._skills_json: str = "[]"

    def set_skills(self, skills: list[dict[str, str]]) -> None:
        self._skills_json = json.dumps(skills, ensure_ascii=False)

    async def start(self, handler: MessageHandler) -> None:
        self._handler = handler
        self._loop = asyncio.get_event_loop()

        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = urlparse(self.path).path

                # API routes
                if path == "/api/skills":
                    self._json_resp(200, json.loads(gateway._skills_json))
                    return

                # Static file serving from web/dist/
                self._serve_static(path)

            def do_POST(self):
                path = urlparse(self.path).path
                if path == "/api/chat":
                    body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                    try:
                        data = json.loads(body)
                    except json.JSONDecodeError:
                        self._json_resp(400, {"error": "Invalid JSON"})
                        return

                    text = data.get("text", "").strip()
                    if not text:
                        self._json_resp(400, {"error": "Empty message"})
                        return

                    conv_id = data.get("conversation_id", f"web-{uuid.uuid4().hex[:8]}")

                    future = asyncio.run_coroutine_threadsafe(
                        gateway._handle_chat(text, conv_id), gateway._loop
                    )
                    try:
                        result = future.result(timeout=120)
                        self._json_resp(200, result)
                    except Exception as e:
                        self._json_resp(500, {"error": str(e)})
                else:
                    self.send_response(404)
                    self.end_headers()

            def _json_resp(self, code: int, data: Any):
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def _serve_static(self, path: str):
                if path == "/" or path == "":
                    path = "/index.html"

                file_path = _WEB_DIST / path.lstrip("/")

                # Security: prevent path traversal
                try:
                    file_path = file_path.resolve()
                    if not str(file_path).startswith(str(_WEB_DIST.resolve())):
                        self.send_response(403)
                        self.end_headers()
                        return
                except (ValueError, OSError):
                    self.send_response(400)
                    self.end_headers()
                    return

                if file_path.is_file():
                    mime, _ = mimetypes.guess_type(str(file_path))
                    content = file_path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", mime or "application/octet-stream")
                    self.send_header("Content-Length", str(len(content)))
                    if "/assets/" in path:
                        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                    self.end_headers()
                    self.wfile.write(content)
                else:
                    # SPA fallback: serve index.html for client-side routing
                    index = _WEB_DIST / "index.html"
                    if index.is_file():
                        content = index.read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Content-Length", str(len(content)))
                        self.end_headers()
                        self.wfile.write(content)
                    else:
                        self.send_response(404)
                        self.end_headers()
                        self.wfile.write(b"Web UI not built. Run: cd web && npm run build")

            def log_message(self, format, *args):
                pass

        self._server = HTTPServer((self.host, self.port), Handler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

        built = "ready" if (_WEB_DIST / "index.html").exists() else "not built (run: cd web && npm run build)"
        print(f"Aiflay Web UI: http://localhost:{self.port}  [{built}]")

        try:
            while self._server:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None

    async def _handle_chat(self, text: str, conversation_id: str) -> dict[str, Any]:
        if not self._handler:
            return {"error": "Agent not ready"}

        message = InboundMessage(
            text=text,
            sender_id="web-user",
            channel="web",
            conversation_id=conversation_id,
        )

        response = await self._handler(message)
        return {
            "text": response.text,
            "conversation_id": response.conversation_id,
            "skills": response.metadata.get("skills", []),
        }
