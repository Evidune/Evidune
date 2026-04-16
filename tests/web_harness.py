"""Browser-test helpers for the web gateway."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from typing import Any

from agent.harness.models import TaskEvent
from gateway.base import InboundMessage, OutboundMessage
from gateway.web import WebGateway
from memory.store import MemoryStore

ROOT = Path(__file__).resolve().parent.parent
WEB_INDEX = ROOT / "web" / "dist" / "index.html"


class DeterministicWebHandler:
    """Deterministic in-process chat handler used by browser tests."""

    def __init__(self, memory: MemoryStore) -> None:
        self.memory = memory
        self.feedback_execution_id: int | None = None

    async def __call__(self, message: InboundMessage) -> OutboundMessage:
        conv_id = message.conversation_id
        mode = message.metadata.get("mode") or "execute"
        self.memory.ensure_conversation(conv_id, channel="web")
        self.memory.set_conversation_mode(conv_id, mode)
        self.memory.add_message(conv_id, "user", message.text)

        lowered = message.text.lower()
        if mode == "plan":
            response = await self._plan_response(message)
        elif "feedback" in lowered:
            response = await self._feedback_response(message)
        else:
            response = await self._streaming_response(message)

        self.memory.add_message(conv_id, "assistant", response.text)
        return response

    async def _streaming_response(self, message: InboundMessage) -> OutboundMessage:
        conv_id = message.conversation_id
        task_events = [
            TaskEvent(
                sequence=1,
                type="phase",
                phase="plan",
                role="planner",
                message="Planner drafted the bounded swarm plan.",
            ),
            TaskEvent(
                sequence=2,
                type="phase",
                phase="execute",
                role="worker-1",
                message="Worker completed the implementation branch.",
            ),
        ]
        sink = message.metadata.get("event_sink")
        if callable(sink):
            for event in task_events:
                sink(event)
                await asyncio.sleep(0.3)
            await asyncio.sleep(0.15)

        self.memory.set_conversation_title(conv_id, "Execute validation")
        return OutboundMessage(
            text="Streaming result ready.",
            conversation_id=conv_id,
            metadata={
                "mode": "execute",
                "task_id": "task-stream-1",
                "squad": "general",
                "task_status": "completed",
                "task_events": [event.to_dict() for event in task_events],
                "convergence_summary": {"decision": "accept"},
                "budget_summary": {
                    "rounds_used": 1,
                    "max_rounds": 2,
                    "tool_calls_used": 1,
                    "tool_call_budget": 4,
                    "token_used": 120,
                    "token_budget": 1000,
                },
            },
        )

    async def _plan_response(self, message: InboundMessage) -> OutboundMessage:
        conv_id = message.conversation_id
        plan = {
            "explanation": "Validate the web gateway through a small browser plan.",
            "items": [
                {"step": "Open the app in plan mode", "status": "completed"},
                {"step": "Render the persisted structured plan", "status": "in_progress"},
            ],
        }
        self.memory.update_conversation_plan(
            conv_id,
            items=plan["items"],
            explanation=plan["explanation"],
        )
        self.memory.set_conversation_title(conv_id, "Plan validation")
        return OutboundMessage(
            text="Plan ready for browser validation.",
            conversation_id=conv_id,
            metadata={
                "mode": "plan",
                "plan": plan,
            },
        )

    async def _feedback_response(self, message: InboundMessage) -> OutboundMessage:
        conv_id = message.conversation_id
        response_text = "Feedback-ready response."
        execution_id = self.memory.record_execution(
            skill_name="browser-feedback",
            user_input=message.text,
            assistant_output=response_text,
            conversation_id=conv_id,
        )
        self.feedback_execution_id = execution_id
        self.memory.set_conversation_title(conv_id, "Feedback validation")
        return OutboundMessage(
            text=response_text,
            conversation_id=conv_id,
            metadata={
                "mode": "execute",
                "skills": ["browser-feedback"],
                "execution_ids": [execution_id],
            },
        )


@dataclass
class RunningWebHarness:
    gateway: WebGateway
    memory: MemoryStore
    handler: DeterministicWebHandler
    loop: asyncio.AbstractEventLoop
    thread: Thread

    @property
    def base_url(self) -> str:
        return self.gateway.base_url

    def close(self) -> None:
        asyncio.run_coroutine_threadsafe(self.gateway.stop(), self.loop).result(timeout=5)
        self.thread.join(timeout=5)
        self.memory.close()


def start_web_harness(db_path: Path) -> RunningWebHarness:
    """Start a real WebGateway on an ephemeral port for browser tests."""
    memory = MemoryStore(db_path)
    handler = DeterministicWebHandler(memory)
    gateway = WebGateway(host="127.0.0.1", port=0)
    gateway.set_memory_store(memory)
    gateway.set_skills([{"name": "browser-feedback", "description": "Feedback skill"}])
    loop = asyncio.new_event_loop()

    def runner() -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(gateway.start(handler))
        finally:
            loop.close()

    thread = Thread(target=runner, daemon=True)
    thread.start()

    deadline = time.time() + 5
    while not gateway.base_url:
        if time.time() >= deadline:
            raise RuntimeError("Timed out waiting for the web gateway to start")
        time.sleep(0.05)

    return RunningWebHarness(
        gateway=gateway,
        memory=memory,
        handler=handler,
        loop=loop,
        thread=thread,
    )


def wait_for(predicate, *, timeout: float = 5, interval: float = 0.05) -> Any:
    """Poll until predicate returns a truthy value or timeout expires."""
    deadline = time.time() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.time() >= deadline:
            raise AssertionError("Timed out waiting for condition")
        time.sleep(interval)
