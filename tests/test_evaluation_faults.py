"""Tests for deterministic benchmark execution faults and wall-time budgets."""

import asyncio

import pytest

from agent.benchmark_executor import BenchmarkProviderTimeout
from core.evaluation_faults import execute_variant
from core.evaluation_models import VariantSpec
from skills.benchmark import BenchmarkExecution, PreparedTask


class _Adapter:
    async def execute(self, prepared, skill_content, model_ref, trial, executor):
        return await executor(prepared, skill_content, model_ref, trial)


@pytest.mark.asyncio
async def test_total_trial_wall_time_is_bounded():
    prepared = PreparedTask(
        corpus_id="fixture",
        task=object(),
        split="development",
        workspace="/tmp/evidune-timeout-test",
        agent_context={"trial_timeout_seconds": 0.01},
    )

    async def hanging_executor(*_args):
        await asyncio.sleep(1)
        return BenchmarkExecution(output="late")

    with pytest.raises(BenchmarkProviderTimeout, match="exceeded 0.01 seconds"):
        await execute_variant(
            adapter=_Adapter(),
            prepared=prepared,
            variant=VariantSpec("candidate", "1", "Skill"),
            model_ref={},
            trial=1,
            executor=hanging_executor,
        )


@pytest.mark.asyncio
async def test_total_trial_timeout_does_not_wait_for_cancellation():
    prepared = PreparedTask(
        corpus_id="fixture",
        task=object(),
        split="development",
        workspace="/tmp/evidune-hard-timeout-test",
        agent_context={"trial_timeout_seconds": 0.01},
    )

    async def cancellation_resistant_executor(*_args):
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            await asyncio.sleep(1)
        return BenchmarkExecution(output="late")

    with pytest.raises(BenchmarkProviderTimeout, match="exceeded 0.01 seconds"):
        await execute_variant(
            adapter=_Adapter(),
            prepared=prepared,
            variant=VariantSpec("candidate", "1", "Skill"),
            model_ref={},
            trial=1,
            executor=cancellation_resistant_executor,
        )


@pytest.mark.asyncio
async def test_inner_provider_timeout_keeps_its_specific_diagnostic():
    prepared = PreparedTask(
        corpus_id="fixture",
        task=object(),
        split="development",
        workspace="/tmp/evidune-provider-timeout-test",
        agent_context={"trial_timeout_seconds": 1},
    )

    async def provider_timeout(*_args):
        raise BenchmarkProviderTimeout("model call exceeded 0.1 seconds")

    with pytest.raises(BenchmarkProviderTimeout, match="model call exceeded 0.1"):
        await execute_variant(
            adapter=_Adapter(),
            prepared=prepared,
            variant=VariantSpec("candidate", "1", "Skill"),
            model_ref={},
            trial=1,
            executor=provider_timeout,
        )
