"""Neutral execution contracts shared by agents and benchmark adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PreparedTask:
    corpus_id: str
    task: Any
    split: str
    workspace: str
    agent_context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchmarkExecution:
    output: str
    final_state: dict[str, Any] = field(default_factory=dict)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchmarkObservation:
    kind: str
    payload: dict[str, Any]
    evidence_ref: str = ""


@dataclass(frozen=True)
class ResetResult:
    ok: bool
    state_digest: str = ""
    reason: str = ""


BenchmarkExecutor = Callable[
    [PreparedTask, str, dict[str, Any], int], Awaitable[BenchmarkExecution]
]
