"""Shared task models for swarm and iteration harness flows."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TaskBrief:
    """Compact context handed to harness-controlled roles."""

    user_input: str
    conversation_id: str = ""
    mode: str = "execute"
    identity_name: str = ""
    history: list[dict[str, str]] = field(default_factory=list)
    facts: list[dict[str, str]] = field(default_factory=list)
    selected_skills: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SquadProfile:
    """Persistent description of a built-in or operator-defined squad."""

    name: str
    worker_branches: int = 1
    roles: list[str] = field(default_factory=list)
    description: str = ""
    tool_heavy_worker: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def role_roster(self) -> list[str]:
        roster = ["planner"]
        roster.extend(f"worker-{idx + 1}" for idx in range(self.worker_branches))
        roster.extend(["critic", "synthesizer"])
        return roster

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkArtifact:
    """One role output persisted by the harness."""

    id: int = 0
    task_id: str = ""
    step_id: int = 0
    phase: str = ""
    role: str = ""
    kind: str = "note"
    summary: str = ""
    content: str = ""
    accepted: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConvergenceRule:
    """Deterministic stop/retry policy for a harness task."""

    max_replans: int = 1
    max_rejections: int = 2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DecisionRecord:
    """Final or intermediate convergence decision."""

    decision: str = "keep"
    rationale: str = ""
    accepted_artifact_ids: list[int] = field(default_factory=list)
    rejected_artifact_ids: list[int] = field(default_factory=list)
    stop_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskEvent:
    """User-visible event emitted while a harness task is running."""

    sequence: int
    type: str
    phase: str = ""
    role: str = ""
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BudgetSummary:
    """Approximate budget tracking for a harness task."""

    token_budget: int = 0
    token_used: int = 0
    tool_call_budget: int = 0
    tool_calls_used: int = 0
    wall_clock_budget_s: int = 0
    elapsed_ms: int = 0
    max_rounds: int = 1
    rounds_used: int = 0
    stopped_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HarnessTask:
    """In-memory representation of one harness-controlled task."""

    id: str
    conversation_id: str
    surface: str
    squad: SquadProfile
    brief: TaskBrief
    status: str = "running"
    events: list[TaskEvent] = field(default_factory=list)
    artifacts: list[WorkArtifact] = field(default_factory=list)
    decision: DecisionRecord | None = None
    final_output: str = ""
    convergence_summary: dict[str, Any] = field(default_factory=dict)
    budget_summary: BudgetSummary = field(default_factory=BudgetSummary)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "surface": self.surface,
            "status": self.status,
            "squad": self.squad.to_dict(),
            "brief": self.brief.to_dict(),
            "events": [event.to_dict() for event in self.events],
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "decision": self.decision.to_dict() if self.decision else None,
            "final_output": self.final_output,
            "convergence_summary": dict(self.convergence_summary),
            "budget_summary": self.budget_summary.to_dict(),
        }
