"""Failure-safe benchmark environment cleanup helpers."""

from __future__ import annotations

from typing import Any

from skills.benchmark import PreparedTask, ResetResult


def safe_reset(adapter: Any, prepared: PreparedTask) -> ResetResult:
    try:
        return adapter.reset(prepared)
    except Exception as exc:
        return ResetResult(
            False, reason=f"Environment reset raised {exc.__class__.__name__}: {exc}"
        )
