"""First-class runtime models for skills."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from skills.loader import Skill


@dataclass(frozen=True)
class SkillRecord:
    """Runtime-facing metadata for a loaded skill."""

    name: str
    description: str
    source: str = "base"
    status: str = "active"
    version: str = "1.0.0"
    path: str = ""
    scripts: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    evaluation_contract: dict | None = None
    created_at: str = ""
    updated_at: str = ""
    last_loaded_at: str = ""
    load_error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SkillMatch:
    """Skill match with diagnostics for logs and resolver decisions."""

    skill: Skill
    score: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.skill.name, "score": self.score, "reasons": list(self.reasons)}


@dataclass(frozen=True)
class SkillSnapshot:
    """Per-turn skill registry snapshot for observability."""

    records: list[SkillRecord]
    matches: list[SkillMatch]
    prompt_token_estimate: int

    def to_log_dict(self) -> dict:
        return {
            "skill_snapshot_count": len(self.records),
            "skill_match_reasons": {m.skill.name: m.reasons for m in self.matches},
            "skill_prompt_token_estimate": self.prompt_token_estimate,
        }


_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]", re.IGNORECASE)
_GENERIC_TOKENS = {
    "a",
    "an",
    "and",
    "for",
    "into",
    "of",
    "skill",
    "skills",
    "capability",
    "capabilities",
    "workflow",
    "workflows",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def skill_tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if t.lower() not in _GENERIC_TOKENS}


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0
