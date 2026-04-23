"""Web UI gateway — serves Svelte frontend + API endpoints."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import queue
import re
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

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
        self._skill_provider: Any = None
        self._memory_store: Any = None  # Optional MemoryStore for /api/feedback

    def set_skills(self, skills: list[dict[str, str]]) -> None:
        self._skills_json = json.dumps(skills, ensure_ascii=False)

    def set_skill_provider(self, provider: Any) -> None:
        """Wire a dynamic skill metadata provider for /api/skills."""
        self._skill_provider = provider

    def set_memory_store(self, store: Any) -> None:
        """Wire a MemoryStore so /api/feedback can persist signals."""
        self._memory_store = store

    @property
    def bound_port(self) -> int:
        """Return the OS-assigned port once the HTTP server is started."""
        if self._server is None:
            return 0
        return int(self._server.server_address[1])

    @property
    def base_url(self) -> str:
        """Read-only base URL for tests and diagnostics after startup."""
        if self._server is None:
            return ""
        public_host = self.host
        if public_host in {"0.0.0.0", "::"}:
            public_host = "127.0.0.1"
        return f"http://{public_host}:{self.bound_port}"

    async def start(self, handler: MessageHandler) -> None:
        self._handler = handler
        self._loop = asyncio.get_event_loop()

        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path

                # API routes
                if path == "/api/skills":
                    self._json_resp(200, gateway._skills_payload())
                    return

                if path == "/api/chat/stream":
                    self._stream_chat(parsed.query)
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

                    identity = data.get("identity")
                    if identity is not None and not isinstance(identity, str):
                        self._json_resp(400, {"error": "identity must be a string"})
                        return
                    identity = identity.strip() if isinstance(identity, str) else None

                    mode = data.get("mode")
                    if mode is not None and not isinstance(mode, str):
                        self._json_resp(400, {"error": "mode must be a string"})
                        return
                    mode = mode.strip() if isinstance(mode, str) else None
                    if mode not in (None, "plan", "execute"):
                        self._json_resp(400, {"error": "mode must be 'plan' or 'execute'"})
                        return

                    conv_id = data.get("conversation_id", f"web-{uuid.uuid4().hex[:8]}")

                    future = asyncio.run_coroutine_threadsafe(
                        gateway._handle_chat(text, conv_id, identity=identity, mode=mode),
                        gateway._loop,
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

            def _stream_chat(self, raw_query: str):
                params = parse_qs(raw_query, keep_blank_values=True)
                text = (params.get("text", [""])[0] or "").strip()
                if not text:
                    self._json_resp(400, {"error": "Empty message"})
                    return
                identity = (params.get("identity", [""])[0] or "").strip() or None
                mode = (params.get("mode", [""])[0] or "").strip() or None
                if mode not in (None, "plan", "execute"):
                    self._json_resp(400, {"error": "mode must be 'plan' or 'execute'"})
                    return
                conv_id = (params.get("conversation_id", [""])[0] or "").strip()
                if not conv_id:
                    conv_id = f"web-{uuid.uuid4().hex[:8]}"

                events: queue.Queue[dict[str, Any]] = queue.Queue()

                def sink(event) -> None:
                    events.put(event.to_dict())

                future = asyncio.run_coroutine_threadsafe(
                    gateway._handle_chat(
                        text,
                        conv_id,
                        identity=identity,
                        mode=mode,
                        event_sink=sink,
                    ),
                    gateway._loop,
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

                while True:
                    if future.done() and events.empty():
                        break
                    try:
                        payload = events.get(timeout=0.2)
                    except queue.Empty:
                        payload = None
                    if payload is not None:
                        self._sse("task", payload)
                try:
                    result = future.result(timeout=5)
                    self._sse("done", result)
                except Exception as exc:
                    self._sse("error", {"error": str(exc)})

            def _sse(self, event: str, data: Any):
                payload = json.dumps(data, ensure_ascii=False)
                body = f"event: {event}\ndata: {payload}\n\n".encode()
                self.wfile.write(body)
                self.wfile.flush()

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
        print(f"Evidune Web UI: {self.base_url or f'http://localhost:{self.port}'}  [{built}]")

        try:
            while self._server:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        if self._server:
            server = self._server
            self._server = None
            server.shutdown()
            server.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None

    async def _handle_chat(
        self,
        text: str,
        conversation_id: str,
        identity: str | None = None,
        mode: str | None = None,
        event_sink: Any = None,
    ) -> dict[str, Any]:
        if not self._handler:
            return {"error": "Agent not ready"}

        metadata: dict[str, Any] = {}
        if identity:
            metadata["identity"] = identity
        if mode:
            metadata["mode"] = mode
        if callable(event_sink):
            metadata["event_sink"] = event_sink

        message = InboundMessage(
            text=text,
            sender_id="web-user",
            channel="web",
            conversation_id=conversation_id,
            metadata=metadata,
        )

        response = await self._handler(message)
        return {
            "text": response.text,
            "conversation_id": response.conversation_id,
            "skills": response.metadata.get("skills", []),
            "execution_ids": response.metadata.get("execution_ids", []),
            "emerged_skill": response.metadata.get("emerged_skill"),
            "skill_creation": response.metadata.get("skill_creation"),
            "facts_extracted": response.metadata.get("facts_extracted", 0),
            "identity": response.metadata.get("identity"),
            "mode": response.metadata.get("mode"),
            "plan": response.metadata.get("plan"),
            "new_title": response.metadata.get("new_title"),
            "tool_trace": response.metadata.get("tool_trace", []),
            "task_id": response.metadata.get("task_id"),
            "squad": response.metadata.get("squad"),
            "task_status": response.metadata.get("task_status"),
            "task_events": response.metadata.get("task_events", []),
            "convergence_summary": response.metadata.get("convergence_summary"),
            "budget_summary": response.metadata.get("budget_summary"),
            "environment_id": response.metadata.get("environment_id"),
            "environment_status": response.metadata.get("environment_status"),
            "validation_summary": response.metadata.get("validation_summary"),
            "delivery_summary": response.metadata.get("delivery_summary"),
            "artifact_manifest": response.metadata.get("artifact_manifest"),
        }

    def _skills_payload(self) -> list[dict[str, Any]]:
        if callable(self._skill_provider):
            try:
                return self._skill_provider()
            except Exception:
                return []
        return json.loads(self._skills_json)

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
        skill_name = execs.get("skill_name", "")
        lifecycle_decision = None
        skill_status = (
            self._memory_store.resolve_skill_status(skill_name) if skill_name else "active"
        )
        harness_task_id = ""
        rolled_back = False
        if skill_name:
            skill_state = self._memory_store.get_skill_state(skill_name)
            skill_path = (skill_state or {}).get("path", "")
            if skill_path or skill_state is not None:
                try:
                    from agent.iteration_harness import IterationHarness, build_decision_packet

                    skill = SimpleNamespace(
                        name=skill_name,
                        path=skill_path,
                        update_section="## Reference Data",
                    )
                    current = (
                        Path(skill_path).read_text(encoding="utf-8")
                        if skill_path and Path(skill_path).is_file()
                        else ""
                    )
                    workflow = IterationHarness(self._memory_store)
                    decision = workflow.run(
                        packet=build_decision_packet(
                            self._memory_store,
                            skill=skill,
                            current=current,
                            result=None,
                            surface="serve",
                            conversation_id=execs.get("conversation_id") or "",
                            task_kind="skill_feedback",
                        )
                    )
                    lifecycle_decision = decision.decision
                    skill_status = decision.skill_status
                    harness_task_id = decision.task.id
                    rolled_back = decision.decision == "rollback"
                except Exception:
                    pass
        return {
            "ok": ok,
            "execution_id": execution_id,
            "signals": existing,
            "rolled_back": rolled_back,
            "lifecycle_decision": lifecycle_decision,
            "skill_status": skill_status,
            "harness_task_id": harness_task_id,
        }

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
