"""Read-only tools for inspecting graph memory."""

from __future__ import annotations

from typing import Any

from agent.tools.base import Tool
from memory.store import MemoryStore


def graph_memory_tools(memory: MemoryStore) -> list[Tool]:
    """Expose graph memory retrieval without mutation capabilities."""

    async def search_graph_memory(query: str, limit: int = 8) -> list[dict[str, Any]]:
        return memory.search_graph_memory_seeds(query, limit=limit)

    async def expand_graph_memory(
        node_ids: list[str], direction: str = "both", limit: int = 20
    ) -> list[dict[str, Any]]:
        return memory.expand_graph_memory(node_ids, direction=direction, limit=limit)

    async def read_graph_memory_content(node_id: str) -> dict[str, Any]:
        node = memory.read_graph_memory_content(node_id)
        if node is None:
            return {"error": "graph_memory_node_not_found", "node_id": node_id}
        return node

    async def explain_graph_memory_trace(trace_id: str) -> dict[str, Any]:
        trace = memory.get_graph_memory_trace(trace_id)
        if trace is None:
            return {"error": "graph_memory_trace_not_found", "trace_id": trace_id}
        return trace

    return [
        Tool(
            name="search_graph_memory",
            description="Search Cue-Tag-Content graph memory nodes by query text.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 8},
                },
                "required": ["query"],
            },
            handler=search_graph_memory,
        ),
        Tool(
            name="expand_graph_memory",
            description="Expand graph memory from one or more node IDs.",
            parameters={
                "type": "object",
                "properties": {
                    "node_ids": {"type": "array", "items": {"type": "string"}},
                    "direction": {
                        "type": "string",
                        "enum": ["out", "in", "both"],
                        "default": "both",
                    },
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["node_ids"],
            },
            handler=expand_graph_memory,
        ),
        Tool(
            name="read_graph_memory_content",
            description="Read a graph memory node by node ID.",
            parameters={
                "type": "object",
                "properties": {"node_id": {"type": "string"}},
                "required": ["node_id"],
            },
            handler=read_graph_memory_content,
        ),
        Tool(
            name="explain_graph_memory_trace",
            description="Read a recorded graph memory reconstruction trace.",
            parameters={
                "type": "object",
                "properties": {"trace_id": {"type": "string"}},
                "required": ["trace_id"],
            },
            handler=explain_graph_memory_trace,
        ),
    ]
