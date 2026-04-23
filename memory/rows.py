"""Row → object conversion helpers for the memory store.

Separated so the store's query methods stay thin and the conversions
live in one place (previously each method built the Fact/dict inline,
duplicating the shape across get/search operations).
"""

from __future__ import annotations

import json
from typing import Any

from memory.store_models import Fact


def row_to_fact(row) -> Fact:
    return Fact(
        key=row["key"],
        value=row["value"],
        source=row["source"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_execution(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "skill_name": row["skill_name"],
        "conversation_id": row["conversation_id"],
        "harness_task_id": row["harness_task_id"] or "",
        "user_input": row["user_input"],
        "assistant_output": row["assistant_output"],
        "signals": json.loads(row["signals_json"] or "{}"),
        "score": row["cross_model_score"],
        "evaluator_reasoning": row["evaluator_reasoning"],
        "created_at": row["created_at"],
    }


def row_to_iteration_run(row, updates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "domain": row["domain"],
        "metrics_adapter": row["metrics_adapter"],
        "metrics_source": row["metrics_source"],
        "sort_metric": row["sort_metric"],
        "total_records": row["total_records"],
        "summary": row["summary"],
        "patterns": json.loads(row["patterns_json"] or "[]"),
        "raw_stats": json.loads(row["raw_stats_json"] or "{}"),
        "top_performers": json.loads(row["top_performers_json"] or "[]"),
        "bottom_performers": json.loads(row["bottom_performers_json"] or "[]"),
        "commit_sha": row["commit_sha"],
        "created_at": row["created_at"],
        "updates": updates or [],
    }


def row_to_harness_task(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "surface": row["surface"],
        "squad_profile": row["squad_profile"],
        "status": row["status"],
        "task_kind": row["task_kind"],
        "user_input": row["user_input"],
        "selected_skills": json.loads(row["selected_skills_json"] or "[]"),
        "role_roster": json.loads(row["role_roster_json"] or "[]"),
        "budget": json.loads(row["budget_json"] or "{}"),
        "environment_id": row["environment_id"] or "",
        "environment_status": row["environment_status"] or "",
        "artifact_manifest": json.loads(row["artifact_manifest_json"] or "{}"),
        "validation_summary": json.loads(row["validation_summary_json"] or "{}"),
        "delivery_summary": json.loads(row["delivery_summary_json"] or "{}"),
        "escalation_reason": row["escalation_reason"] or "",
        "summary": row["summary"],
        "convergence": json.loads(row["convergence_json"] or "{}"),
        "final_output": row["final_output"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_harness_step(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "phase": row["phase"],
        "role": row["role"],
        "status": row["status"],
        "summary": row["summary"],
        "tool_trace": json.loads(row["tool_trace_json"] or "[]"),
        "budget": json.loads(row["budget_json"] or "{}"),
        "created_at": row["created_at"],
    }


def row_to_harness_artifact(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "step_id": row["step_id"],
        "phase": row["phase"],
        "role": row["role"],
        "kind": row["kind"],
        "summary": row["summary"],
        "content": row["content"],
        "accepted": bool(row["accepted"]),
        "meta": json.loads(row["meta_json"] or "{}"),
        "created_at": row["created_at"],
    }


def row_to_skill_state(row) -> dict[str, Any]:
    return {
        "skill_name": row["skill_name"],
        "origin": row["origin"],
        "path": row["path"],
        "status": row["status"],
        "reason": row["reason"],
        "evidence": json.loads(row["evidence_json"] or "{}"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_skill_lifecycle_event(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "skill_name": row["skill_name"],
        "action": row["action"],
        "status": row["status"],
        "path": row["path"],
        "harness_task_id": row["harness_task_id"] or "",
        "reason": row["reason"],
        "evidence": json.loads(row["evidence_json"] or "{}"),
        "content_before": row["content_before"],
        "content_after": row["content_after"],
        "created_at": row["created_at"],
    }


def row_to_outcome_observation(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "skill_name": row["skill_name"],
        "entity_id": row["entity_id"],
        "observed_at": row["observed_at"] or "",
        "metrics": json.loads(row["metrics_json"] or "{}"),
        "dimensions": json.loads(row["dimensions_json"] or "{}"),
        "source": row["source"] or "",
        "skill_version": row["skill_version"] or "",
        "run_id": row["run_id"] or 0,
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "created_at": row["created_at"],
    }


def row_to_outcome_summary(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "skill_name": row["skill_name"],
        "primary_kpi": row["primary_kpi"],
        "sample_count": row["sample_count"],
        "baseline_value": row["baseline_value"],
        "current_value": row["current_value"],
        "delta": row["delta"],
        "confidence": row["confidence"],
        "window": json.loads(row["window_json"] or "{}"),
        "segment_breakdown": json.loads(row["segment_breakdown_json"] or "[]"),
        "policy_state": json.loads(row["policy_state_json"] or "{}"),
        "raw_stats": json.loads(row["raw_stats_json"] or "{}"),
        "exemplar_slice": json.loads(row["exemplar_slice_json"] or "[]"),
        "run_id": row["run_id"] or 0,
        "created_at": row["created_at"],
    }
