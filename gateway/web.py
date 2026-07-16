"""Web UI gateway — serves Svelte frontend + API endpoints."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import socket
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse

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
        self._server: uvicorn.Server | None = None
        self._socket: socket.socket | None = None
        self._ready = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._skills_json: str = "[]"
        self._skill_provider: Any = None
        self._skill_lookup: Any = None
        self._skill_change_handler: Any = None
        self._memory_store: Any = None  # Optional MemoryStore for /api/feedback

    def set_skills(self, skills: list[dict[str, str]]) -> None:
        self._skills_json = json.dumps(skills, ensure_ascii=False)

    def set_skill_provider(self, provider: Any) -> None:
        """Wire a dynamic skill metadata provider for /api/skills."""
        self._skill_provider = provider

    def set_skill_lookup(self, lookup: Any) -> None:
        """Wire runtime Skill lookup without coupling the gateway to the skills package."""
        self._skill_lookup = lookup

    def set_skill_change_handler(self, handler: Any) -> None:
        """Wire live registry synchronization after feedback-driven changes."""
        self._skill_change_handler = handler

    def set_memory_store(self, store: Any) -> None:
        """Wire a MemoryStore so /api/feedback can persist signals."""
        self._memory_store = store

    @property
    def bound_port(self) -> int:
        """Return the OS-assigned port once the HTTP server is started."""
        if self._socket is None:
            return 0
        return int(self._socket.getsockname()[1])

    @property
    def base_url(self) -> str:
        """Read-only base URL for tests and diagnostics after startup."""
        if self._socket is None or not self._ready:
            return ""
        public_host = self.host
        if public_host in {"0.0.0.0", "::"}:
            public_host = "127.0.0.1"
        return f"http://{public_host}:{self.bound_port}"

    async def start(self, handler: MessageHandler) -> None:
        self._handler = handler
        self._loop = asyncio.get_event_loop()
        app = self._build_app()
        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level="warning",
            lifespan="off",
        )
        self._server = uvicorn.Server(config)

        try:
            serve_task = asyncio.create_task(self._server.serve())
            while not self._server.started:
                if serve_task.done():
                    await serve_task
                await asyncio.sleep(0.01)
            if self._server.servers and self._server.servers[0].sockets:
                self._socket = self._server.servers[0].sockets[0]
            self._ready = True
            built = (
                "ready"
                if (_WEB_DIST / "index.html").exists()
                else "not built (run: cd web && npm run build)"
            )
            print(f"Evidune Web UI: {self.base_url or f'http://localhost:{self.port}'}  [{built}]")
            await serve_task
        except asyncio.CancelledError:
            if self._server:
                self._server.should_exit = True
            raise
        finally:
            self._ready = False
            self._socket = None
            self._server = None

    async def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        await asyncio.sleep(0)

    def _build_app(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @app.get("/api/skills")
        async def skills():
            return JSONResponse(self._skills_payload())

        @app.get("/api/chat/stream")
        async def chat_stream(
            text: str = "",
            identity: str = "",
            mode: str = "",
            conversation_id: str = "",
        ):
            text = text.strip()
            if not text:
                return JSONResponse({"error": "Empty message"}, status_code=400)
            normalized_mode = mode.strip() or None
            if normalized_mode not in (None, "plan", "execute"):
                return JSONResponse({"error": "mode must be 'plan' or 'execute'"}, status_code=400)
            conv_id = conversation_id.strip() or f"web-{uuid.uuid4().hex[:8]}"

            async def event_stream():
                events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

                def sink(event) -> None:
                    payload = event.to_dict() if hasattr(event, "to_dict") else dict(event)
                    events.put_nowait(payload)

                task = asyncio.create_task(
                    self._handle_chat(
                        text,
                        conv_id,
                        identity=identity.strip() or None,
                        mode=normalized_mode,
                        event_sink=sink,
                    )
                )
                while True:
                    if task.done() and events.empty():
                        break
                    try:
                        payload = await asyncio.wait_for(events.get(), timeout=0.2)
                    except TimeoutError:
                        continue
                    yield self._sse("task", payload)
                try:
                    yield self._sse("done", await task)
                except Exception as exc:
                    yield self._sse("error", {"error": str(exc)})

            return StreamingResponse(
                event_stream(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )

        @app.get("/api/conversations")
        async def conversations():
            return JSONResponse(self._list_conversations())

        @app.get("/api/conversations/{conversation_id}/history")
        async def conversation_history(conversation_id: str):
            return JSONResponse(self._conversation_history(conversation_id))

        @app.get("/api/conversations/{conversation_id}/context")
        async def conversation_context(conversation_id: str):
            result = self._conversation_context(conversation_id)
            return JSONResponse(result, status_code=200 if "error" not in result else 404)

        @app.get("/api/conversations/{conversation_id}")
        async def get_conversation(conversation_id: str):
            result = self._get_conversation(conversation_id)
            return JSONResponse(result, status_code=200 if "error" not in result else 404)

        @app.post("/api/conversations/{conversation_id}/archive")
        async def archive_conversation(conversation_id: str):
            result = self._set_status(conversation_id, "archived")
            return JSONResponse(result, status_code=200 if "error" not in result else 404)

        @app.post("/api/conversations/{conversation_id}/unarchive")
        async def unarchive_conversation(conversation_id: str):
            result = self._set_status(conversation_id, "active")
            return JSONResponse(result, status_code=200 if "error" not in result else 404)

        @app.delete("/api/conversations/{conversation_id}")
        async def delete_conversation(conversation_id: str):
            result = self._delete_conversation(conversation_id)
            return JSONResponse(result, status_code=200 if "error" not in result else 404)

        @app.post("/api/chat")
        async def chat(request: Request):
            try:
                data = await request.json()
            except json.JSONDecodeError:
                return JSONResponse({"error": "Invalid JSON"}, status_code=400)
            if not isinstance(data, dict):
                return JSONResponse({"error": "Invalid JSON"}, status_code=400)
            text = str(data.get("text", "")).strip()
            if not text:
                return JSONResponse({"error": "Empty message"}, status_code=400)
            identity = data.get("identity")
            if identity is not None and not isinstance(identity, str):
                return JSONResponse({"error": "identity must be a string"}, status_code=400)
            mode = data.get("mode")
            if mode is not None and not isinstance(mode, str):
                return JSONResponse({"error": "mode must be a string"}, status_code=400)
            normalized_mode = mode.strip() if isinstance(mode, str) else None
            if normalized_mode not in (None, "plan", "execute"):
                return JSONResponse({"error": "mode must be 'plan' or 'execute'"}, status_code=400)
            conv_id = data.get("conversation_id") or f"web-{uuid.uuid4().hex[:8]}"
            try:
                result = await self._handle_chat(
                    text,
                    str(conv_id),
                    identity=identity.strip() if isinstance(identity, str) else None,
                    mode=normalized_mode,
                )
            except Exception as exc:
                return JSONResponse({"error": str(exc)}, status_code=500)
            return JSONResponse(result)

        @app.post("/api/feedback")
        async def feedback(request: Request):
            try:
                data = await request.json()
            except json.JSONDecodeError:
                return JSONResponse({"error": "Invalid JSON"}, status_code=400)
            if not isinstance(data, dict):
                return JSONResponse({"error": "Invalid JSON"}, status_code=400)
            result = self._handle_feedback(data)
            return JSONResponse(result, status_code=200 if "error" not in result else 400)

        @app.get("/{path:path}")
        async def static_or_spa(path: str):
            return self._static_response("/" + path)

        return app

    def _sse(self, event: str, data: Any) -> str:
        payload = json.dumps(data, ensure_ascii=False)
        return f"event: {event}\ndata: {payload}\n\n"

    def _static_response(self, path: str) -> FileResponse | PlainTextResponse:
        if path == "/" or path == "":
            path = "/index.html"

        file_path = _WEB_DIST / path.lstrip("/")
        try:
            file_path = file_path.resolve()
            if not str(file_path).startswith(str(_WEB_DIST.resolve())):
                return PlainTextResponse("Forbidden", status_code=403)
        except (ValueError, OSError):
            return PlainTextResponse("Bad request", status_code=400)

        if file_path.is_file():
            mime, _ = mimetypes.guess_type(str(file_path))
            headers = {}
            if "/assets/" in path:
                headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return FileResponse(
                file_path, media_type=mime or "application/octet-stream", headers=headers
            )

        index = _WEB_DIST / "index.html"
        if index.is_file():
            return FileResponse(index, media_type="text/html; charset=utf-8")
        return PlainTextResponse("Web UI not built. Run: cd web && npm run build", status_code=404)

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
            "execution_evaluations": response.metadata.get("execution_evaluations", []),
            "outcome_governance": response.metadata.get("outcome_governance", []),
            "facts_extracted": response.metadata.get("facts_extracted", 0),
            "identity": response.metadata.get("identity"),
            "mode": response.metadata.get("mode"),
            "plan": response.metadata.get("plan"),
            "new_title": response.metadata.get("new_title"),
            "tool_trace": response.metadata.get("tool_trace", []),
            "tool_observations_saved": response.metadata.get("tool_observations_saved", 0),
            "context_detail": response.metadata.get("context_detail"),
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
            skill = self._skill_lookup(skill_name) if self._skill_lookup else None
            skill_path = (skill_state or {}).get("path", "") or str(getattr(skill, "path", ""))
            if skill_path or skill_state is not None:
                try:
                    from agent.iteration_harness import IterationHarness, build_decision_packet

                    path = Path(skill_path)
                    if skill is None:
                        skill = SimpleNamespace(
                            name=skill_name,
                            version=str(execs.get("skill_version") or ""),
                            path=skill_path,
                            update_section="## Reference Data",
                            execution_contract=None,
                            outcome_contract=None,
                        )
                    current = path.read_text(encoding="utf-8") if path.is_file() else ""
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
                    if self._skill_change_handler and (
                        decision.decision in {"disable", "confirm"} or decision.update.has_changes
                    ):
                        self._skill_change_handler(skill_name, skill_path, skill_status)
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
        history = self._memory_store.get_history(conv_id, limit=None)
        return {"conversation": dict(meta), "messages": history}

    def _conversation_context(self, conv_id: str) -> dict[str, Any]:
        if not self._memory_store:
            return {"error": "Memory store not configured"}
        if not self._memory_store.get_conversation(conv_id):
            return {"error": f"Conversation {conv_id} not found"}
        report = self._memory_store.get_context_report(conv_id)
        return {
            "conversation_id": conv_id,
            "available": report is not None,
            "context": report,
        }

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
