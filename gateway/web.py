"""Web UI gateway — serves Svelte frontend + API endpoints."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import urlparse

from gateway.base import Gateway, InboundMessage, MessageHandler

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
        self._memory_store: Any = None  # Optional MemoryStore for /api/feedback

    def set_skills(self, skills: list[dict[str, str]]) -> None:
        self._skills_json = json.dumps(skills, ensure_ascii=False)

    def set_memory_store(self, store: Any) -> None:
        """Wire a MemoryStore so /api/feedback can persist signals."""
        self._memory_store = store

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

                if path == "/api/conversations":
                    self._json_resp(200, gateway._list_conversations())
                    return

                # /api/conversations/<id>/history
                m = re.match(r"^/api/conversations/([^/]+)/history$", path)
                if m:
                    self._json_resp(200, gateway._conversation_history(m.group(1)))
                    return

                # /api/conversations/<id>  (metadata only)
                m = re.match(r"^/api/conversations/([^/]+)$", path)
                if m:
                    result = gateway._get_conversation(m.group(1))
                    code = 200 if "error" not in result else 404
                    self._json_resp(code, result)
                    return

                # Static file serving from web/dist/
                self._serve_static(path)

            def do_POST(self):
                path = urlparse(self.path).path

                # /api/conversations/<id>/archive
                m = re.match(r"^/api/conversations/([^/]+)/archive$", path)
                if m:
                    result = gateway._set_status(m.group(1), "archived")
                    code = 200 if "error" not in result else 404
                    self._json_resp(code, result)
                    return

                # /api/conversations/<id>/unarchive
                m = re.match(r"^/api/conversations/([^/]+)/unarchive$", path)
                if m:
                    result = gateway._set_status(m.group(1), "active")
                    code = 200 if "error" not in result else 404
                    self._json_resp(code, result)
                    return

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

                    persona = data.get("persona")
                    if persona is not None and not isinstance(persona, str):
                        self._json_resp(400, {"error": "persona must be a string"})
                        return
                    persona = persona.strip() if isinstance(persona, str) else None

                    conv_id = data.get("conversation_id", f"web-{uuid.uuid4().hex[:8]}")

                    future = asyncio.run_coroutine_threadsafe(
                        gateway._handle_chat(text, conv_id, persona=persona), gateway._loop
                    )
                    try:
                        result = future.result(timeout=120)
                        self._json_resp(200, result)
                    except Exception as e:
                        self._json_resp(500, {"error": str(e)})

                elif path == "/api/feedback":
                    body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                    try:
                        data = json.loads(body)
                    except json.JSONDecodeError:
                        self._json_resp(400, {"error": "Invalid JSON"})
                        return
                    result = gateway._handle_feedback(data)
                    code = 200 if "error" not in result else 400
                    self._json_resp(code, result)

                else:
                    self.send_response(404)
                    self.end_headers()

            def do_DELETE(self):
                path = urlparse(self.path).path
                m = re.match(r"^/api/conversations/([^/]+)$", path)
                if m:
                    result = gateway._delete_conversation(m.group(1))
                    code = 200 if "error" not in result else 404
                    self._json_resp(code, result)
                    return
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

        built = (
            "ready"
            if (_WEB_DIST / "index.html").exists()
            else "not built (run: cd web && npm run build)"
        )
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

    async def _handle_chat(
        self, text: str, conversation_id: str, persona: str | None = None
    ) -> dict[str, Any]:
        if not self._handler:
            return {"error": "Agent not ready"}

        message = InboundMessage(
            text=text,
            sender_id="web-user",
            channel="web",
            conversation_id=conversation_id,
            metadata={"persona": persona} if persona else {},
        )

        response = await self._handler(message)
        return {
            "text": response.text,
            "conversation_id": response.conversation_id,
            "skills": response.metadata.get("skills", []),
            "execution_ids": response.metadata.get("execution_ids", []),
            "emerged_skill": response.metadata.get("emerged_skill"),
            "facts_extracted": response.metadata.get("facts_extracted", 0),
            "persona": response.metadata.get("persona"),
            "new_title": response.metadata.get("new_title"),
            "tool_trace": response.metadata.get("tool_trace", []),
        }

    def _handle_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist a user feedback signal for a previous execution.

        Expected payload:
          {execution_id: int, signal: str, value: bool|int|str}

        signal must be one of: thumbs_up, thumbs_down, copied,
        regenerated, rating.
        """
        execution_id = payload.get("execution_id")
        signal_type = payload.get("signal")
        value = payload.get("value", True)

        if not isinstance(execution_id, int) or not signal_type:
            return {"error": "execution_id (int) and signal (str) required"}

        if not self._memory_store:
            return {"error": "Memory store not configured"}

        # Read existing signals, merge new one, write back
        execs = self._memory_store.get_skill_executions_by_id(execution_id)
        if not execs:
            return {"error": f"Execution {execution_id} not found"}

        existing = execs.get("signals", {})
        existing[signal_type] = value
        ok = self._memory_store.update_execution_signals(execution_id, existing)
        return {"ok": ok, "execution_id": execution_id, "signals": existing}

    # --- Conversation management ---

    def _list_conversations(self) -> list[dict[str, Any]]:
        if not self._memory_store:
            return []
        return self._memory_store.list_conversations(channel="web")

    def _conversation_history(self, conv_id: str) -> dict[str, Any]:
        if not self._memory_store:
            return {"error": "Memory store not configured"}
        meta = self._memory_store.get_conversation(conv_id)
        if not meta:
            return {"error": f"Conversation {conv_id} not found"}
        history = self._memory_store.get_history(conv_id, limit=200)
        return {"conversation": dict(meta), "messages": history}

    def _get_conversation(self, conv_id: str) -> dict[str, Any]:
        if not self._memory_store:
            return {"error": "Memory store not configured"}
        meta = self._memory_store.get_conversation(conv_id)
        if not meta:
            return {"error": f"Conversation {conv_id} not found"}
        return dict(meta)

    def _set_status(self, conv_id: str, status: str) -> dict[str, Any]:
        if not self._memory_store:
            return {"error": "Memory store not configured"}
        ok = self._memory_store.set_conversation_status(conv_id, status)
        if not ok:
            return {"error": f"Conversation {conv_id} not found"}
        return {"ok": True, "id": conv_id, "status": status}

    def _delete_conversation(self, conv_id: str) -> dict[str, Any]:
        if not self._memory_store:
            return {"error": "Memory store not configured"}
        ok = self._memory_store.delete_conversation(conv_id)
        if not ok:
            return {"error": f"Conversation {conv_id} not found"}
        return {"ok": True, "id": conv_id}
