"""Shared graph memory models and deterministic token helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from memory.lexical import lexical_terms

SOURCE_PRIORITY = {
    "skill": 5,
    "skill_reference": 4,
    "fact": 3,
    "harness_artifact": 2,
    "message": 1,
}


@dataclass
class ReconstructedContext:
    trace_id: str = ""
    selected_nodes: list[dict[str, Any]] = field(default_factory=list)
    selected_skills: list[str] = field(default_factory=list)
    evidence_items: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "selected_nodes": [node["node_id"] for node in self.selected_nodes],
            "selected_skills": list(self.selected_skills),
            "evidence_count": len(self.evidence_items),
            "actions": list(self.actions),
        }


def extract_cues(text: str, *, max_cues: int = 30) -> list[str]:
    return lexical_terms(text, limit=max_cues)
