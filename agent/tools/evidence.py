"""Controlled tool for committing delayed evidence after an Agent execution."""

from __future__ import annotations

from agent.tools.base import Tool
from skills.governance import canonical_digest


def evidence_commitment_tools() -> list[Tool]:
    """Describe delayed evidence to bind after the current execution is persisted."""

    async def commit_outcome_evidence(
        entity_type: str,
        entity_id: str,
        observation_plan: dict,
        skill_name: str = "",
        intervention: dict | None = None,
        expected_state: dict | None = None,
        forbidden_state: dict | None = None,
        attribution_policy: str = "unknown",
        minimum_evidence_grade: str = "unknown",
    ) -> dict:
        grades = {"direct", "controlled", "supported", "observational", "unknown"}
        if attribution_policy not in grades or minimum_evidence_grade not in grades:
            raise ValueError("Invalid evidence attribution grade")
        if not entity_type.strip() or not entity_id.strip():
            raise ValueError("Evidence commitment requires an entity type and stable id")
        horizons = observation_plan.get("horizons") if isinstance(observation_plan, dict) else None
        if not isinstance(horizons, list) or not horizons:
            raise ValueError("Evidence commitment requires at least one observation horizon")
        for horizon in horizons:
            if not isinstance(horizon, dict) or not str(horizon.get("id") or "").strip():
                raise ValueError("Every observation horizon requires an id")
            for identity in (
                "probe_id",
                "probe_revision",
                "evaluator_id",
                "evaluator_revision",
            ):
                if not str(horizon.get(identity) or observation_plan.get(identity) or "").strip():
                    raise ValueError(f"Evidence commitment requires {identity}")
        payload = {
            "kind": "evidence_commitment",
            "skill_name": skill_name,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "intervention": intervention or {},
            "expected_state": expected_state or {},
            "forbidden_state": forbidden_state or {},
            "observation_plan": observation_plan,
            "attribution_policy": attribution_policy,
            "minimum_evidence_grade": minimum_evidence_grade,
        }
        payload["commitment_digest"] = canonical_digest(payload)
        return payload

    return [
        Tool(
            name="commit_outcome_evidence",
            description=(
                "Commit a stable external entity and an allowlisted delayed-observation plan "
                "to the current Skill execution. This records provenance only; it does not "
                "run arbitrary code or contact the external system."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string"},
                    "entity_type": {"type": "string"},
                    "entity_id": {"type": "string"},
                    "intervention": {"type": "object"},
                    "expected_state": {"type": "object"},
                    "forbidden_state": {"type": "object"},
                    "observation_plan": {
                        "type": "object",
                        "description": (
                            "Versioned probe/evaluator ids, arguments, retry policy, and horizons"
                        ),
                    },
                    "attribution_policy": {
                        "type": "string",
                        "enum": [
                            "direct",
                            "controlled",
                            "supported",
                            "observational",
                            "unknown",
                        ],
                    },
                    "minimum_evidence_grade": {
                        "type": "string",
                        "enum": [
                            "direct",
                            "controlled",
                            "supported",
                            "observational",
                            "unknown",
                        ],
                    },
                },
                "required": ["entity_type", "entity_id", "observation_plan"],
            },
            handler=commit_outcome_evidence,
        )
    ]
