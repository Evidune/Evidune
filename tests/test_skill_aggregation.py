from skills.aggregation import aggregate_version_evidence


def _result(execution_id, version, verdict, *, grade="direct", failures=None, hard=None):
    return {
        "execution_id": execution_id,
        "skill_name": "writer",
        "skill_version": version,
        "evaluator_id": "state",
        "evaluator_revision": "v1",
        "contract_digest": "contract-1",
        "verdict": verdict,
        "attribution_grade": grade,
        "failure_modes": failures or [],
        "hard_gate_failures": hard or [],
    }


def test_aggregation_never_pools_versions_or_invalid_results():
    results = [
        _result(1, "1.0", "fail", failures=["skipped_verification"]),
        _result(2, "2.0", "fail", failures=["skipped_verification"]),
        _result(3, "1.0", "invalid", failures=["provider_error"]),
    ]

    aggregate = aggregate_version_evidence(
        results,
        skill_name="writer",
        skill_version="1.0",
    )

    assert aggregate.contributing_execution_ids == [1]
    assert aggregate.actionable_failure_modes == {"skipped_verification": 1}
    assert aggregate.excluded_execution_ids == {"different_version": [2], "invalid": [3]}


def test_weak_attribution_is_visible_but_not_actionable():
    aggregate = aggregate_version_evidence(
        [
            _result(
                1,
                "1.0",
                "fail",
                grade="observational",
                failures=["low_engagement"],
                hard=["unsafe_write"],
            )
        ],
        skill_name="writer",
        skill_version="1.0",
    )

    assert aggregate.failure_mode_counts == {"low_engagement": 1}
    assert aggregate.actionable_failure_modes == {}
    assert aggregate.hard_gate_failures[0]["attribution_grade"] == "observational"


def test_advisory_llm_judge_is_excluded_from_actionable_evidence():
    result = _result(9, "1.0", "fail", failures=["style"], hard=["claimed_gate"])
    result["evaluator_type"] = "llm_judge"
    result["metadata"] = {"advisory": True}

    aggregate = aggregate_version_evidence(
        [result],
        skill_name="writer",
        skill_version="1.0",
    )

    assert aggregate.actionable_failure_modes == {}
    assert aggregate.hard_gate_failures == []
    assert aggregate.excluded_execution_ids == {"advisory": [9]}
