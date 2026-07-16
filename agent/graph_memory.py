"""Cue-Tag-Content graph memory reconstruction for agent context selection."""

from __future__ import annotations

from typing import Any

from agent.graph_memory_indexer import sync_graph_sources
from agent.graph_memory_models import SOURCE_PRIORITY, ReconstructedContext, extract_cues
from memory.store import Fact, MemoryStore
from skills.loader import Skill


class GraphMemoryService:
    """Build and traverse a lightweight associative memory graph."""

    def __init__(
        self,
        memory: MemoryStore,
        *,
        enabled: bool = True,
        index_sources: list[str] | None = None,
        max_seed_nodes: int = 8,
        max_traversal_steps: int = 4,
        max_context_items: int = 12,
    ) -> None:
        self.memory = memory
        self.enabled = enabled
        self.index_sources = index_sources or [
            "facts",
            "skills",
            "messages",
            "harness_artifacts",
        ]
        self.max_seed_nodes = max_seed_nodes
        self.max_traversal_steps = max_traversal_steps
        self.max_context_items = max_context_items

    @classmethod
    def from_config(cls, memory: MemoryStore, config: object) -> GraphMemoryService:
        return cls(
            memory,
            enabled=bool(getattr(config, "enabled", True)),
            index_sources=list(getattr(config, "index_sources", []) or []),
            max_seed_nodes=int(getattr(config, "max_seed_nodes", 8)),
            max_traversal_steps=int(getattr(config, "max_traversal_steps", 4)),
            max_context_items=int(getattr(config, "max_context_items", 12)),
        )

    def reconstruct(
        self,
        query: str,
        *,
        conversation_id: str = "",
        facts: list[Fact] | None = None,
        history: list[dict[str, str]] | None = None,
        skills: list[Skill] | None = None,
    ) -> ReconstructedContext:
        if not self.enabled:
            return ReconstructedContext()

        self.sync_sources(
            conversation_id=conversation_id,
            facts=facts or [],
            history=history or [],
            skills=skills or [],
        )

        seeds = self.memory.search_graph_memory_seeds(query, limit=self.max_seed_nodes)
        seed_ids = [node["node_id"] for node in seeds]
        candidates: dict[str, dict[str, Any]] = {node["node_id"]: node for node in seeds}
        actions: list[dict[str, Any]] = []
        frontier = seed_ids

        for step in range(self.max_traversal_steps):
            if not frontier:
                break
            expanded = self.memory.expand_graph_memory(frontier, direction="out", limit=64)
            next_frontier: list[str] = []
            for item in expanded:
                edge = item["edge"]
                node = item["node"]
                actions.append(
                    {
                        "step": step + 1,
                        "action": edge["edge_type"],
                        "from": edge["from_node_id"],
                        "to": edge["to_node_id"],
                        "weight": edge["weight"],
                    }
                )
                existing = candidates.get(node["node_id"])
                if existing is None or edge["weight"] > existing.get("_edge_weight", 0):
                    node["_edge_weight"] = edge["weight"]
                    candidates[node["node_id"]] = node
                if node["node_id"] not in seed_ids:
                    next_frontier.append(node["node_id"])
            frontier = [node_id for node_id in next_frontier if node_id not in seed_ids]

        selected = self._select_content(query, list(candidates.values()))
        selected_skills = self._selected_skills(selected)
        trace_id = self.memory.record_graph_memory_trace(
            query=query,
            seed_nodes=seed_ids,
            selected_nodes=[node["node_id"] for node in selected],
            selected_skills=selected_skills,
            actions=actions,
        )
        return ReconstructedContext(
            trace_id=trace_id,
            selected_nodes=selected,
            selected_skills=selected_skills,
            evidence_items=[self._evidence_item(node) for node in selected],
            actions=actions,
        )

    def sync_sources(
        self,
        *,
        conversation_id: str = "",
        facts: list[Fact],
        history: list[dict[str, str]],
        skills: list[Skill],
    ) -> None:
        sync_graph_sources(
            self.memory,
            self.index_sources,
            conversation_id=conversation_id,
            facts=facts,
            history=history,
            skills=skills,
        )

    def _select_content(self, query: str, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        query_tokens = set(extract_cues(query))
        content_nodes = [node for node in nodes if node.get("node_type") == "content"]
        scored: list[tuple[float, dict[str, Any]]] = []
        for node in content_nodes:
            text_tokens = set(extract_cues(f"{node.get('key', '')} {node.get('text', '')}"))
            overlap = len(query_tokens & text_tokens)
            score = (
                overlap * 2.0
                + float(node.get("_edge_weight", 0) or 0)
                + SOURCE_PRIORITY.get(node.get("source_type", ""), 0)
            )
            if score > 0 and overlap > 0:
                scored.append((score, node))
        scored.sort(key=lambda item: (item[0], item[1].get("updated_at", "")), reverse=True)
        return [node for _, node in scored[: self.max_context_items]]

    def _selected_skills(self, nodes: list[dict[str, Any]]) -> list[str]:
        skills: list[str] = []
        for node in nodes:
            metadata = node.get("metadata") or {}
            skill_name = metadata.get("skill_name")
            if isinstance(skill_name, str) and skill_name and skill_name not in skills:
                skills.append(skill_name)
        return skills

    def _evidence_item(self, node: dict[str, Any]) -> dict[str, Any]:
        return {
            "node_id": node["node_id"],
            "source_type": node.get("source_type", ""),
            "source_id": node.get("source_id", ""),
            "key": node.get("key", ""),
            "text": node.get("text", ""),
            "metadata": node.get("metadata", {}),
        }
