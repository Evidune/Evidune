"""Shared graph memory models and deterministic token helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9_./-]+|[\u4e00-\u9fff]", re.IGNORECASE)
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "for",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "should",
    "the",
    "this",
    "to",
    "use",
    "with",
    "work",
}
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
    cues: list[str] = []
    for raw in _TOKEN_RE.findall(text or ""):
        token = raw.lower().strip("._/-")
        if len(token) < 2 or token in _STOPWORDS:
            continue
        if token not in cues:
            cues.append(token)
        if len(cues) >= max_cues:
            break
    return cues
