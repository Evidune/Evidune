"""Tests for score-optional typed Skill governance."""

import pytest

from skills.governance import (
    AttributionGrade,
    EvaluationResult,
    EvaluationVerdict,
    GovernancePolicy,
    canonical_digest,
    decide_governance,
)


def _result(**overrides) -> EvaluationResult:
    payload = {
        "evaluator_id": "state",
        "evaluator_revision": "v1",
        "evaluator_type": "state_diff",
        "verdict": "pass",
        "execution_id": 1,
        "attribution_grade": "direct",
    }
    payload.update(overrides)
    return EvaluationResult(**payload)


def test_result_does_not_require_numeric_score():
    result = _result(dimensions={"expected_state_reached": True})

    assert result.score is None
    assert result.to_dict()["verdict"] == "pass"
    assert result.to_dict()["dimensions"] == {"expected_state_reached": True}


def test_non_finite_score_is_invalid():
    with pytest.raises(ValueError, match="finite"):
        _result(score=float("nan"))


def test_hard_gate_failure_cannot_be_offset_by_high_score():
    result = _result(
        score=1.0,
        verdict="pass",
        hard_gate_failures=["unauthorized_write"],
    )

    decision = decide_governance([result])

    assert decision.verdict == EvaluationVerdict.FAIL
    assert decision.promotable is False
    assert decision.hard_gate_failures == ["unauthorized_write"]


def test_llm_judge_alone_is_advisory_and_cannot_promote():
    judge = _result(
        evaluator_id="judge",
        evaluator_type="llm_judge",
        score=0.99,
    )

    decision = decide_governance([judge])

    assert decision.verdict == EvaluationVerdict.INCONCLUSIVE
    assert decision.promotable is False
    assert decision.advisory_evaluator_ids == ["judge"]


def test_llm_judge_cannot_create_an_authoritative_hard_gate():
    judge = _result(
        evaluator_id="judge",
        evaluator_type="llm_judge",
        verdict="fail",
        failure_modes=["semantic_concern"],
        hard_gate_failures=["claimed_safety_failure"],
    )

    decision = decide_governance([judge])

    assert decision.verdict == EvaluationVerdict.INCONCLUSIVE
    assert decision.hard_gate_failures == []
    assert decision.failure_modes == []


def test_required_evaluator_and_attribution_are_enforced():
    result = _result(attribution_grade=AttributionGrade.OBSERVATIONAL)
    policy = GovernancePolicy(
        required_evaluators=["state", "trace"],
        minimum_attribution=AttributionGrade.SUPPORTED,
    )

    missing = decide_governance([result], policy)
    assert missing.missing_evaluators == ["trace"]
    assert missing.promotable is False

    weak = decide_governance(
        [result, _result(evaluator_id="trace", attribution_grade="observational")], policy
    )
    assert weak.reason == "attribution grade is below policy minimum"


def test_canonical_digest_ignores_mapping_order():
    assert canonical_digest({"a": 1, "b": [2]}) == canonical_digest({"b": [2], "a": 1})
