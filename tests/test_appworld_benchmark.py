from __future__ import annotations

import sys
import types
from dataclasses import replace
from pathlib import Path

import pytest

from adapters.appworld_benchmark import AppWorldBenchmarkAdapter
from adapters.benchmark import BenchmarkExecution, CorpusTask, EvaluationCorpus, PreparedTask
from agent.appworld_executor import AppWorldLLMBenchmarkExecutor
from agent.benchmark_executor import BenchmarkBudgetExceeded, InvalidBenchmarkResponse
from agent.llm.base import LLMClient
from agent.tools.base import CompletionResult, ToolCall


class _Evaluation:
    def to_dict(self):
        return {
            "success": True,
            "passes": [{"requirement": "state changed", "label": "no_op_fail"}],
            "failures": [],
            "difficulty": 2,
        }


class _Task:
    instruction = "Create the requested record"
    app_descriptions = {"notes": "Manage notes"}
    api_docs = {
        "supervisor": {
            "complete_task": {
                "description": "Complete the active task",
                "parameters": [],
            }
        }
    }


class _World:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.task = _Task()
        self.output_directory = "/tmp/appworld-output"
        self.completed = False
        self.closed = False
        self.calls = []
        self.__class__.instances.append(self)

    def execute(self, code):
        self.calls.append(code)
        if "complete_task" in code:
            self.completed = True
        return {"ok": True}

    def task_completed(self):
        return self.completed

    def evaluate(self):
        return _Evaluation()

    def close(self):
        self.closed = True


def _corpus() -> EvaluationCorpus:
    task = CorpusTask("task-1", "loaded by adapter", metadata={"appworld_task_id": "aw-1"})
    return EvaluationCorpus(
        corpus_id="appworld-pilot",
        schema_version=1,
        adapter="appworld",
        adapter_revision="v1",
        sources=[],
        tasks={task.id: task},
        splits={"development": [task.id]},
        environment={"experiment_name": "test-run"},
        budget={"max_tool_calls_per_trial": 5},
    )


@pytest.mark.asyncio
async def test_appworld_adapter_keeps_evaluation_outside_executor_context(
    monkeypatch, tmp_path: Path
):
    module = types.ModuleType("appworld")
    module.AppWorld = _World
    monkeypatch.setitem(sys.modules, "appworld", module)
    adapter = AppWorldBenchmarkAdapter()
    corpus = _corpus()
    task = corpus.tasks["task-1"]
    prepared = adapter.prepare(corpus, task, "development", tmp_path)

    assert adapter.reset(prepared).ok
    assert prepared.agent_context["instruction"] == _Task.instruction
    assert "ground_truth" not in prepared.agent_context

    async def executor(prepared, skill, model_ref, trial):
        world = prepared.agent_context["appworld_world"]
        world.execute("apis.supervisor.complete_task()")
        return BenchmarkExecution(output="done", tool_trace=[{"name": "appworld_execute"}])

    execution = await adapter.execute(prepared, "# Skill", {}, 1, executor)
    results = adapter.evaluate(prepared, execution, 42, "v1")

    assert execution.final_state == {"task_completed": True, "appworld_success": True}
    assert results[0].verdict.value == "pass"
    assert results[0].score is None
    assert results[0].attribution_grade.value == "direct"
    assert adapter.reset(prepared).ok
    assert _World.instances[-1].closed


@pytest.mark.asyncio
async def test_appworld_adapter_requires_explicit_task_completion(monkeypatch, tmp_path: Path):
    module = types.ModuleType("appworld")
    module.AppWorld = _World
    monkeypatch.setitem(sys.modules, "appworld", module)
    adapter = AppWorldBenchmarkAdapter()
    corpus = _corpus()
    prepared = adapter.prepare(corpus, corpus.tasks["task-1"], "development", tmp_path)
    assert adapter.reset(prepared).ok

    async def executor(prepared, skill, model_ref, trial):
        return BenchmarkExecution(output="claimed completion")

    execution = await adapter.execute(prepared, "# Skill", {}, 1, executor)
    result = adapter.evaluate(prepared, execution, 42, "v1")[0]

    assert execution.final_state["appworld_success"] is True
    assert result.verdict.value == "fail"
    assert result.dimensions["task_completed"] is False
    assert adapter.reset(prepared).ok


def test_appworld_adapter_isolates_repeated_trial_outputs(tmp_path: Path):
    adapter = AppWorldBenchmarkAdapter()
    corpus = _corpus()
    task = corpus.tasks["task-1"]

    first = adapter.prepare(corpus, task, "development", tmp_path / "trial-1")
    second = adapter.prepare(corpus, task, "development", tmp_path / "trial-2")

    assert first.agent_context["experiment_name"] != second.agent_context["experiment_name"]
    assert first.agent_context["experiment_name"].startswith("test-run-")


def test_appworld_adapter_sets_isolated_data_root(monkeypatch, tmp_path: Path):
    roots = []
    path_store_module = types.ModuleType("appworld.common.path_store")
    path_store_module.path_store = types.SimpleNamespace(update_root=roots.append)
    module = types.ModuleType("appworld")
    module.AppWorld = _World
    monkeypatch.setitem(sys.modules, "appworld", module)
    monkeypatch.setitem(sys.modules, "appworld.common.path_store", path_store_module)
    corpus = replace(_corpus(), environment={"experiment_name": "test-run", "root": str(tmp_path)})
    adapter = AppWorldBenchmarkAdapter()
    prepared = adapter.prepare(
        corpus, corpus.tasks["task-1"], "development", tmp_path / "workspace"
    )

    assert adapter.reset(prepared).ok
    assert roots == [str(tmp_path.resolve())]
    assert adapter.reset(prepared).ok


class _ToolLLM(LLMClient):
    def __init__(self):
        self.turn = 0
        self.messages = []

    async def complete(self, messages, **kwargs):
        return "iteration cap"

    async def complete_with_tools(self, messages, tools, **kwargs):
        self.messages = messages
        self.turn += 1
        if self.turn == 1:
            return CompletionResult(
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="appworld_execute",
                        arguments={"code": "apis.supervisor.complete_task()"},
                    )
                ]
            )
        return CompletionResult(text="Completed")


@pytest.mark.asyncio
async def test_appworld_executor_uses_real_tool_loop_without_hidden_evaluator(tmp_path: Path):
    world = _World()
    prepared = PreparedTask(
        corpus_id="pilot",
        task=CorpusTask("task-1", "placeholder"),
        split="development",
        workspace=str(tmp_path),
        agent_context={
            "appworld_world": world,
            "instruction": "Do the visible task",
            "app_descriptions": {"notes": "Manage notes"},
            "max_model_turns": 3,
            "max_tool_calls": 3,
        },
    )
    llm = _ToolLLM()
    executor = AppWorldLLMBenchmarkExecutor(llm)

    result = await executor(prepared, "Always verify before completing.", {"temperature": 0}, 1)

    assert result.output == "Task completed in AppWorld."
    assert result.final_state["task_completed"] is True
    assert result.tool_trace[0]["name"] == "appworld_execute"
    assert result.tool_trace[0]["api_calls"] == ["supervisor.complete_task"]
    assert llm.turn == 1
    prompt = "\n".join(str(message.get("content") or "") for message in llm.messages)
    assert "Always verify" in prompt
    assert "ground_truth" not in prompt


@pytest.mark.asyncio
async def test_appworld_executor_stops_at_model_turn_budget(tmp_path: Path):
    class LoopingLLM(_ToolLLM):
        async def complete_with_tools(self, messages, tools, **kwargs):
            return CompletionResult(
                tool_calls=[
                    ToolCall(
                        id="loop",
                        name="appworld_execute",
                        arguments={"code": "apis.api_docs.show_app_descriptions()"},
                    )
                ]
            )

    prepared = PreparedTask(
        corpus_id="pilot",
        task=CorpusTask("task-1", "placeholder"),
        split="development",
        workspace=str(tmp_path),
        agent_context={
            "appworld_world": _World(),
            "instruction": "Do the visible task",
            "max_model_turns": 1,
            "max_tool_calls": 3,
        },
    )

    with pytest.raises(BenchmarkBudgetExceeded, match="exhausted 1 model turns") as error:
        await AppWorldLLMBenchmarkExecutor(LoopingLLM())(prepared, "Skill", {}, 1)

    assert error.value.diagnostics["task_completed"] is False
    assert len(error.value.diagnostics["tool_trace"]) == 1


@pytest.mark.asyncio
async def test_appworld_executor_caps_each_model_call_wall_time(tmp_path: Path):
    class SlowLLM(_ToolLLM):
        async def complete_with_tools(self, messages, tools, **kwargs):
            import asyncio

            await asyncio.sleep(1)
            return CompletionResult(text="late")

    prepared = PreparedTask(
        corpus_id="pilot",
        task=CorpusTask("task-1", "placeholder"),
        split="development",
        workspace=str(tmp_path),
        agent_context={
            "appworld_world": _World(),
            "instruction": "Do the visible task",
            "max_model_turns": 2,
            "max_tool_calls": 3,
            "model_call_timeout_seconds": 0.01,
        },
    )

    with pytest.raises(TimeoutError, match="exceeded 0.01 seconds") as error:
        await AppWorldLLMBenchmarkExecutor(SlowLLM())(prepared, "Skill", {}, 1)

    assert error.value.diagnostics["tool_trace"] == []


@pytest.mark.asyncio
async def test_appworld_executor_hard_timeout_does_not_wait_for_cancellation(tmp_path: Path):
    class CancellationResistantLLM(_ToolLLM):
        async def complete_with_tools(self, messages, tools, **kwargs):
            import asyncio

            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                await asyncio.sleep(1)
            return CompletionResult(text="late")

    prepared = PreparedTask(
        corpus_id="pilot",
        task=CorpusTask("task-1", "placeholder"),
        split="development",
        workspace=str(tmp_path),
        agent_context={
            "appworld_world": _World(),
            "instruction": "Do the visible task",
            "max_model_turns": 2,
            "max_tool_calls": 3,
            "model_call_timeout_seconds": 0.01,
        },
    )

    with pytest.raises(TimeoutError, match="exceeded 0.01 seconds"):
        await AppWorldLLMBenchmarkExecutor(CancellationResistantLLM())(prepared, "Skill", {}, 1)


@pytest.mark.asyncio
async def test_appworld_executor_rejects_empty_model_turn(tmp_path: Path):
    class EmptyLLM(_ToolLLM):
        async def complete_with_tools(self, messages, tools, **kwargs):
            return CompletionResult()

    prepared = PreparedTask(
        corpus_id="pilot",
        task=CorpusTask("task-1", "placeholder"),
        split="holdout",
        workspace=str(tmp_path),
        agent_context={
            "appworld_world": _World(),
            "instruction": "Do the visible task",
            "max_model_turns": 2,
            "max_tool_calls": 3,
        },
    )

    with pytest.raises(InvalidBenchmarkResponse, match="neither text nor tool calls"):
        await AppWorldLLMBenchmarkExecutor(EmptyLLM())(prepared, "Skill", {}, 1)


@pytest.mark.asyncio
async def test_appworld_executor_exposes_structured_docs_and_api_calls(tmp_path: Path):
    class DirectToolLLM(_ToolLLM):
        async def complete_with_tools(self, messages, tools, **kwargs):
            self.turn += 1
            if self.turn == 1:
                return CompletionResult(
                    tool_calls=[
                        ToolCall(
                            id="search",
                            name="appworld_search_api_docs",
                            arguments={"query": "complete", "app": "supervisor"},
                        )
                    ]
                )
            return CompletionResult(
                tool_calls=[
                    ToolCall(
                        id="call",
                        name="appworld_call_api",
                        arguments={
                            "app": "supervisor",
                            "api": "complete_task",
                            "arguments_json": "{}",
                        },
                    )
                ]
            )

    world = _World()
    prepared = PreparedTask(
        corpus_id="pilot",
        task=CorpusTask("task-1", "placeholder"),
        split="development",
        workspace=str(tmp_path),
        agent_context={
            "appworld_world": world,
            "instruction": "Do the visible task",
            "max_model_turns": 3,
            "max_tool_calls": 3,
        },
    )

    result = await AppWorldLLMBenchmarkExecutor(DirectToolLLM())(
        prepared, "Use documented APIs.", {}, 1
    )

    assert result.final_state["task_completed"] is True
    assert result.tool_trace[0]["name"] == "appworld_search_api_docs"
    assert result.tool_trace[1]["api_calls"] == ["supervisor.complete_task"]
