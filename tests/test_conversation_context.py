"""Tests for durable token-budgeted conversation context."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.conversation_context import ConversationContextManager, estimate_tokens
from agent.llm import LLMClient
from memory.store import MemoryStore


class SummaryLLM(LLMClient):
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, **kwargs):
        self.calls += 1
        return f"持久摘要版本 {self.calls}：保留目标、约束和未完成工作。"


@pytest.fixture
def memory(tmp_path: Path):
    store = MemoryStore(tmp_path / "context.db")
    yield store
    store.close()


def test_estimate_tokens_counts_chinese_without_latin_division():
    assert estimate_tokens("记忆上下文") == 5
    assert estimate_tokens("abcd") == 1


@pytest.mark.asyncio
async def test_summary_covers_old_messages_and_full_transcript_is_retained(
    memory: MemoryStore,
):
    llm = SummaryLLM()
    manager = ConversationContextManager(
        memory,
        llm,
        recent_token_budget=90,
        summary_token_budget=40,
        tool_observation_token_budget=40,
    )
    for index in range(8):
        memory.add_message(
            "conv",
            "user" if index % 2 == 0 else "assistant",
            f"第{index}条消息：" + ("这是需要保留的长期上下文。" * 4),
        )

    context = await manager.build("conv")

    records = memory.get_history_records("conv", limit=None)
    summary = memory.get_conversation_summary("conv")
    assert len(records) == 8
    assert context.report["transcript"]["preserved_in_full"] is True
    assert context.report["recent_messages"]["count"] < len(records)
    assert summary["covered_through_message_id"] < records[-1]["id"]
    assert summary["covered_through_message_id"] == (
        context.report["recent_messages"]["first_message_id"] - 1
    )
    assert context.summary.startswith("持久摘要版本")
    assert llm.calls >= 1


@pytest.mark.asyncio
async def test_summary_advances_when_recent_tail_moves(memory: MemoryStore):
    llm = SummaryLLM()
    manager = ConversationContextManager(
        memory,
        llm,
        recent_token_budget=70,
        summary_token_budget=40,
        tool_observation_token_budget=40,
    )
    for index in range(5):
        memory.add_message("conv", "user", f"旧消息 {index} " + ("内容" * 20))
    await manager.build("conv")
    first_coverage = memory.get_conversation_summary("conv")["covered_through_message_id"]

    for index in range(3):
        memory.add_message("conv", "assistant", f"新消息 {index} " + ("内容" * 20))
    context = await manager.build("conv")

    summary = memory.get_conversation_summary("conv")
    assert summary["covered_through_message_id"] > first_coverage
    assert len(memory.get_history_records("conv", limit=None)) == 8
    assert context.report["summary"]["source_message_count"] >= 1


@pytest.mark.asyncio
async def test_oversized_old_message_is_summarized_in_full_before_coverage_advances(
    memory: MemoryStore,
):
    llm = SummaryLLM()
    manager = ConversationContextManager(
        memory,
        llm,
        recent_token_budget=40,
        summary_token_budget=30,
        tool_observation_token_budget=0,
    )
    oversized_id = memory.add_message("conv", "user", "超长消息" + ("完整内容" * 80))
    memory.add_message("conv", "assistant", "最近回复")

    await manager.build("conv")

    summary = memory.get_conversation_summary("conv")
    assert summary["covered_through_message_id"] == oversized_id
    assert summary["source_message_count"] == 1
    assert llm.calls > 1


@pytest.mark.asyncio
async def test_compact_tool_observations_respect_budget(memory: MemoryStore):
    llm = SummaryLLM()
    manager = ConversationContextManager(
        memory,
        llm,
        recent_token_budget=100,
        summary_token_budget=40,
        tool_observation_token_budget=30,
    )
    message_id = memory.add_message("conv", "user", "run the command")
    saved = manager.persist_tool_trace(
        "conv",
        message_id,
        [
            {
                "name": "shell",
                "arguments": {"command": "pytest"},
                "result": "成功 " + ("详细输出 " * 500),
                "is_error": False,
            }
        ],
    )

    context = await manager.build("conv")

    assert saved == 1
    assert len(memory.list_tool_observations("conv", limit=None)) == 1
    assert len(context.tool_observations) == 1
    assert context.report["tool_observations"]["estimated_tokens"] <= 30
