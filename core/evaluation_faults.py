"""Deterministic execution faults used to validate evaluator coverage."""

from __future__ import annotations

import asyncio
from typing import Any

from adapters.benchmark import BenchmarkExecutor
from agent.benchmark_executor import BenchmarkProviderTimeout
from core.evaluation_models import VariantSpec
from skills.benchmark import BenchmarkExecution, PreparedTask


async def execute_variant(
    *,
    adapter: Any,
    prepared: PreparedTask,
    variant: VariantSpec,
    model_ref: dict[str, Any],
    trial: int,
    executor: BenchmarkExecutor,
) -> BenchmarkExecution:
    """Execute a variant, applying deterministic faults where prompt mutation is noisy."""

    async def run() -> BenchmarkExecution:
        if variant.mutation_operator != "skip_execution":
            return await adapter.execute(prepared, variant.content, model_ref, trial, executor)

        async def no_op_executor(*_args: Any) -> BenchmarkExecution:
            return BenchmarkExecution(
                output="Known-bad mutation skipped all requested external changes.",
                final_state={"evidune_fault": "skip_execution"},
                tool_trace=[
                    {
                        "name": "evidune_fault_injection",
                        "operator": "skip_execution",
                        "result": "external execution suppressed",
                    }
                ],
                metadata={
                    "trial": trial,
                    "mutation_operator": "skip_execution",
                    "fault_injected": True,
                },
            )

        return await adapter.execute(prepared, variant.content, model_ref, trial, no_op_executor)

    timeout = float(prepared.agent_context.get("trial_timeout_seconds") or 0)
    if timeout <= 0:
        return await run()
    task = asyncio.create_task(run())
    done, _pending = await asyncio.wait({task}, timeout=timeout)
    if task in done:
        return task.result()
    task.cancel()
    await asyncio.sleep(0)
    raise BenchmarkProviderTimeout(f"Benchmark trial exceeded {timeout:g} seconds total wall time")
