"""Real-LLM executor for benchmark tasks with a strict artifact envelope."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
from typing import Any

from agent.llm import LLMClient
from agent.utils import parse_json_response
from skills.benchmark import BenchmarkExecution, PreparedTask


class InvalidBenchmarkResponse(ValueError):
    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or {}


class BenchmarkBudgetExceeded(RuntimeError):
    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or {}


class BenchmarkProviderTimeout(TimeoutError):
    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or {}


_JSON_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


async def complete_with_tools_hard_timeout(
    llm: LLMClient,
    messages: list[dict[str, Any]],
    tools: list[Any],
    *,
    timeout: float,
    temperature: float,
) -> Any:
    """Run one tool-using model turn behind a hard wall-clock timeout."""

    loop = asyncio.get_running_loop()
    result = loop.create_future()

    def settle(value: Any = None, error: BaseException | None = None) -> None:
        if result.done():
            return
        if error is not None:
            result.set_exception(error)
        else:
            result.set_result(value)

    def publish(value: Any = None, error: BaseException | None = None) -> None:
        if loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(settle, value, error)
        except RuntimeError:
            pass

    def worker() -> None:
        try:
            completion = asyncio.run(
                llm.complete_with_tools(messages, tools, temperature=temperature)
            )
        except BaseException as exc:
            publish(error=exc)
        else:
            publish(value=completion)

    threading.Thread(target=worker, daemon=True).start()
    return await asyncio.wait_for(result, timeout=timeout)


class LiveLLMBenchmarkExecutor:
    """Call a configured real LLM without exposing hidden evaluator state."""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def __call__(
        self,
        prepared: PreparedTask,
        skill_content: str,
        model_ref: dict[str, Any],
        trial: int,
    ) -> BenchmarkExecution:
        initial_state = prepared.task.initial_state
        prompt = (
            "Execute the user task using the supplied Skill instructions. "
            "Do not invent access to tools or state that are not present.\n\n"
            "# Skill\n\n"
            f"{skill_content}\n\n"
            "# User task\n\n"
            f"{prepared.task.instruction}\n\n"
            "# Initial state\n\n"
            f"{json.dumps(initial_state, ensure_ascii=False, sort_keys=True)}\n\n"
            "Return only JSON with this shape:\n"
            '{"output":"user-facing result","final_state":{},"tool_trace":[]}\n'
            "`final_state` must describe the state after following the Skill. "
            "`tool_trace` must contain only actions actually taken."
        )
        raw = await self.llm.complete(
            [
                {
                    "role": "system",
                    "content": "You are a benchmarked agent. Follow the Skill and emit strict JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=model_ref.get("temperature", 0),
        )
        payload = parse_json_response(raw, hint_pattern=_JSON_RE)
        if payload is None:
            raise InvalidBenchmarkResponse(f"Unparseable benchmark response: {raw[:200]}")
        output = payload.get("output")
        final_state = payload.get("final_state")
        tool_trace = payload.get("tool_trace")
        if not isinstance(output, str) or not isinstance(final_state, dict):
            raise InvalidBenchmarkResponse(
                "Benchmark response requires output text and final_state"
            )
        if not isinstance(tool_trace, list):
            tool_trace = []
        return BenchmarkExecution(
            output=output,
            final_state=final_state,
            tool_trace=[item for item in tool_trace if isinstance(item, dict)],
            metadata={
                "trial": trial,
                "model_ref": model_ref,
                "raw_response_digest": hashlib.sha256(raw.encode()).hexdigest(),
            },
        )
