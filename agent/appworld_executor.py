"""Tool-using real-LLM executor for AppWorld benchmark tasks."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from agent.benchmark_executor import (
    BenchmarkBudgetExceeded,
    BenchmarkProviderTimeout,
    InvalidBenchmarkResponse,
    complete_with_tools_hard_timeout,
)
from agent.llm import LLMClient
from agent.tools.base import Tool, ToolCall
from agent.tools.registry import ToolRegistry
from skills.benchmark import BenchmarkExecution, PreparedTask


def _openai_tool_call(call: ToolCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
    }


def _search_api_docs(world: Any, query: str, app: str = "") -> list[dict[str, Any]]:
    tokens = {token for token in re.findall(r"[a-zA-Z0-9_]+", query.casefold()) if token}
    matches: list[tuple[int, dict[str, Any]]] = []
    collection = world.task.api_docs
    for app_name in collection:
        if app and app_name != app:
            continue
        for api_name, raw in collection[app_name].items():
            document = dict(raw)
            searchable = " ".join(
                [app_name, api_name, str(document.get("description") or "")]
            ).casefold()
            score = sum(token in searchable for token in tokens)
            if tokens and score == 0:
                continue
            matches.append(
                (
                    score,
                    {
                        "app": app_name,
                        "api": api_name,
                        "description": document.get("description"),
                        "parameters": document.get("parameters") or [],
                    },
                )
            )
    matches.sort(key=lambda item: (-item[0], item[1]["app"], item[1]["api"]))
    return [document for _score, document in matches[:12]]


class AppWorldLLMBenchmarkExecutor:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def __call__(
        self,
        prepared: PreparedTask,
        skill_content: str,
        model_ref: dict[str, Any],
        trial: int,
    ) -> BenchmarkExecution:
        context = prepared.agent_context
        world = context.get("appworld_world")
        if world is None:
            raise InvalidBenchmarkResponse("AppWorld executor received no initialized world")
        max_model_turns = int(context.get("max_model_turns") or 12)
        max_tool_calls = int(context.get("max_tool_calls") or 30)
        model_call_timeout = float(context.get("model_call_timeout_seconds") or 120)
        tool_trace: list[dict[str, Any]] = []

        def diagnostics() -> dict[str, Any]:
            output_directory = str(getattr(world, "output_directory", "") or "")
            return {
                "tool_trace": tool_trace,
                "task_completed": bool(world.task_completed()),
                "output_directory": output_directory,
                "transcript_digest": hashlib.sha256(
                    json.dumps(messages, default=str, sort_keys=True).encode()
                ).hexdigest(),
            }

        async def execute_code(code: str) -> Any:
            if len(code.encode("utf-8")) > 50_000:
                raise ValueError("AppWorld code exceeds the 50KB per-call limit")
            return world.execute(code)

        async def search_api_docs(query: str, app: str = "") -> Any:
            return _search_api_docs(world, query, app)

        async def call_api(app: str, api: str, arguments_json: str) -> Any:
            if not re.fullmatch(r"[a-zA-Z_]\w*", app) or not re.fullmatch(r"[a-zA-Z_]\w*", api):
                raise ValueError("AppWorld app and API names must be identifiers")
            if app not in world.task.api_docs or api not in world.task.api_docs[app]:
                raise ValueError(f"Unknown AppWorld API: {app}.{api}")
            arguments = json.loads(arguments_json or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("arguments_json must encode an object")
            encoded = json.dumps(arguments, ensure_ascii=False)
            code = (
                "import json as _evidune_json\n"
                f"_evidune_args = _evidune_json.loads({json.dumps(encoded)})\n"
                f"_evidune_result = apis.{app}.{api}(**_evidune_args)\n"
                "print(_evidune_result)"
            )
            result = world.execute(code)
            if str(result).lstrip().startswith("Execution failed."):
                raise RuntimeError(str(result))
            return result

        registry = ToolRegistry()
        registry.register(
            Tool(
                name="appworld_execute",
                description=(
                    "Execute Python code in AppWorld. Call APIs as "
                    "apis.<app>.<api>(...). Variables persist across calls. Use "
                    "apis.api_docs to discover APIs and call "
                    "apis.supervisor.complete_task(...) when finished."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Python code to execute"}
                    },
                    "required": ["code"],
                    "additionalProperties": False,
                },
                handler=execute_code,
            )
        )
        registry.register(
            Tool(
                name="appworld_search_api_docs",
                description=(
                    "Search visible AppWorld API documentation. Use this before calling an "
                    "unknown API. Optionally restrict to one app."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "app": {"type": "string", "default": ""},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=search_api_docs,
            )
        )
        registry.register(
            Tool(
                name="appworld_call_api",
                description=(
                    "Call one documented AppWorld API with a JSON object of arguments. "
                    "Prefer this for individual calls; use appworld_execute for loops or "
                    "multi-call code."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "app": {"type": "string"},
                        "api": {"type": "string"},
                        "arguments_json": {
                            "type": "string",
                            "description": "JSON object containing the API arguments",
                        },
                    },
                    "required": ["app", "api", "arguments_json"],
                    "additionalProperties": False,
                },
                handler=call_api,
            )
        )
        instruction = str(context.get("instruction") or prepared.task.instruction)
        apps = context.get("app_descriptions") or {}
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are operating a stateful AppWorld sandbox. Follow the supplied Skill, "
                    "use the structured documentation and API-call tools for individual calls, "
                    "and use appworld_execute for loops or multi-call code. Avoid unrelated side "
                    "effects and never claim an action you did not execute. The benchmark "
                    "evaluator is hidden and must not be inferred or queried."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"# Skill\n\n{skill_content}\n\n"
                    f"# Task\n\n{instruction}\n\n"
                    "# Available apps\n\n"
                    f"{json.dumps(apps, ensure_ascii=False, sort_keys=True)}"
                ),
            },
        ]
        final_text = ""
        for _ in range(max_model_turns):
            try:
                completion = await complete_with_tools_hard_timeout(
                    self.llm,
                    messages,
                    registry.all(),
                    timeout=model_call_timeout,
                    temperature=model_ref.get("temperature", 0),
                )
            except TimeoutError as exc:
                raise BenchmarkProviderTimeout(
                    f"AppWorld model call exceeded {model_call_timeout:g} seconds",
                    diagnostics=diagnostics(),
                ) from exc
            if not completion.tool_calls:
                final_text = completion.text
                if not (final_text or "").strip() and not world.task_completed():
                    raise InvalidBenchmarkResponse(
                        "AppWorld model returned neither text nor tool calls",
                        diagnostics=diagnostics(),
                    )
                break
            if len(tool_trace) + len(completion.tool_calls) > max_tool_calls:
                raise BenchmarkBudgetExceeded(
                    f"AppWorld trial exceeded {max_tool_calls} tool calls",
                    diagnostics=diagnostics(),
                )
            messages.append(
                {
                    "role": "assistant",
                    "content": completion.text or None,
                    "tool_calls": [_openai_tool_call(call) for call in completion.tool_calls],
                    "_evidune_tool_calls": [
                        {"id": call.id, "name": call.name, "arguments": call.arguments}
                        for call in completion.tool_calls
                    ],
                }
            )
            for call in completion.tool_calls:
                result = await registry.execute(call)
                if call.name == "appworld_call_api":
                    api_calls = [f"{call.arguments.get('app')}.{call.arguments.get('api')}"]
                else:
                    api_calls = sorted(
                        {
                            f"{app}.{api}"
                            for app, api in re.findall(
                                r"apis\.([a-zA-Z_]\w*)\.([a-zA-Z_]\w*)",
                                str(call.arguments.get("code") or ""),
                            )
                        }
                    )
                tool_trace.append(
                    {
                        "name": call.name,
                        "arguments": call.arguments,
                        "result": result.content,
                        "is_error": result.is_error,
                        "api_calls": api_calls,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result.content,
                    }
                )
            if world.task_completed():
                final_text = completion.text or "Task completed in AppWorld."
                break
        else:
            raise BenchmarkBudgetExceeded(
                f"AppWorld trial exhausted {max_model_turns} model turns",
                diagnostics=diagnostics(),
            )

        return BenchmarkExecution(
            output=final_text,
            final_state={"task_completed": bool(world.task_completed())},
            tool_trace=tool_trace,
            metadata={
                "trial": trial,
                "model_ref": model_ref,
                "transcript_digest": hashlib.sha256(
                    json.dumps(messages, default=str, sort_keys=True).encode()
                ).hexdigest(),
            },
        )
