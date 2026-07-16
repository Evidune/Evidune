"""Read-only conversation browsing and context diagnostics."""

from __future__ import annotations

from agent.tools.base import Tool
from memory.store import MemoryStore


def conversation_tools(memory: MemoryStore, current_conversation_id: str) -> list[Tool]:
    """Let the LLM browse conversations and inspect assembled context."""

    async def list_conversations(limit: int = 10) -> list[dict]:
        convs = memory.list_conversations(limit=limit)
        return [
            {
                "id": item["id"],
                "title": item["title"] or "(untitled)",
                "preview": item["preview"],
                "updated_at": item["updated_at"],
                "is_current": item["id"] == current_conversation_id,
            }
            for item in convs
        ]

    async def read_conversation(conversation_id: str, limit: int = 20) -> list[dict]:
        return memory.get_history(conversation_id, limit=limit)

    async def context_detail(conversation_id: str = "") -> dict:
        target = conversation_id or current_conversation_id
        report = memory.get_context_report(target)
        if report is None:
            return {
                "conversation_id": target,
                "available": False,
                "message": "No assembled context report is available yet.",
            }
        return {"conversation_id": target, "available": True, **report}

    return [
        Tool(
            name="list_conversations",
            description="List recent conversations (id, title, preview).",
            parameters={
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 10}},
            },
            handler=list_conversations,
        ),
        Tool(
            name="read_conversation",
            description="Read the message history of another conversation by id.",
            parameters={
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["conversation_id"],
            },
            handler=read_conversation,
        ),
        Tool(
            name="context_detail",
            description=(
                "Inspect the last assembled prompt context: token budgets, transcript "
                "retention, summary coverage, recent messages, tools, and memory evidence."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "conversation_id": {
                        "type": "string",
                        "description": "Defaults to the current conversation.",
                    }
                },
            },
            handler=context_detail,
        ),
    ]
