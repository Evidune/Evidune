"""Tests for agent/skill_feedback.py."""

import pytest

from agent.skill_feedback import summarise_skill_feedback


class TestSummariseSkillFeedback:
    def test_uses_signals_and_scores(self):
        summary = summarise_skill_feedback(
            [
                {"signals": {"thumbs_up": True}, "score": 0.8},
                {"signals": {"copied": True}, "score": 0.9},
            ]
        )
        assert summary.signal_samples == 2
        # Scores are recency-weighted: (0.8 + 0.85 * 0.9) / (1 + 0.85)
        assert summary.average_score == pytest.approx(1.565 / 1.85)
        assert summary.combined_confidence > 0.0
        assert summary.should_rewrite is True
        assert summary.should_disable is False

    def test_negative_evidence_requests_disable(self):
        summary = summarise_skill_feedback(
            [
                {"signals": {"thumbs_down": True}, "score": 0.1},
                {"signals": {"regenerated": True}, "score": 0.2},
            ]
        )
        assert summary.should_disable is True
        assert summary.should_rewrite is False

    def test_empty_evidence_never_requests_rewrite(self):
        summary = summarise_skill_feedback([])
        assert summary.signal_samples == 0
        assert summary.average_score is None
        assert summary.should_rewrite is False
        assert summary.should_disable is False

    def test_single_thumbs_down_does_not_disable(self):
        summary = summarise_skill_feedback([{"signals": {"thumbs_down": True}}])
        assert summary.signal_samples == 1
        assert summary.combined_confidence < 0.0
        assert summary.should_disable is False

    def test_sparse_positive_evidence_does_not_rewrite(self):
        summary = summarise_skill_feedback([{"signals": {"thumbs_up": True}}])
        assert summary.signal_samples == 1
        assert summary.should_rewrite is False

    def test_sufficient_negative_evidence_still_disables(self):
        summary = summarise_skill_feedback(
            [
                {"signals": {"thumbs_down": True}, "score": 0.1},
                {"signals": {"thumbs_down": True}, "score": 0.1},
            ]
        )
        assert summary.signal_samples + summary.score_samples >= 3
        assert summary.should_disable is True

    def test_weak_signals_never_dilute_thumbs_within_execution(self):
        # Per-execution normalisation: a copied event in the same execution
        # as a thumbs_down cannot soften the strong negative.
        summary = summarise_skill_feedback([{"signals": {"thumbs_down": True, "copied": True}}])
        assert summary.signal_confidence == -1.0

    def test_single_execution_with_correlated_signals_does_not_disable(self):
        # thumbs_down + regenerate on the same reply is one interaction; its
        # per-signal samples are correlated and must not clear the gate alone,
        # even with the automatic evaluator score on the same execution.
        summary = summarise_skill_feedback(
            [{"signals": {"thumbs_down": True, "regenerated": True}, "score": 0.9}]
        )
        assert summary.signal_samples + summary.score_samples >= 3
        assert summary.should_disable is False

    def test_two_bad_executions_with_correlated_signals_still_disable(self):
        summary = summarise_skill_feedback(
            [
                {"signals": {"thumbs_down": True, "regenerated": True}, "score": 0.9},
                {"signals": {"thumbs_down": True, "regenerated": True}, "score": 0.9},
            ]
        )
        assert summary.should_disable is True

    def test_many_signals_on_one_execution_do_not_disable(self):
        summary = summarise_skill_feedback(
            [{"signals": {"thumbs_down": True, "regenerated": True, "edited_request": True}}]
        )
        assert summary.signal_samples == 3
        assert summary.should_disable is False

    def test_single_low_score_cannot_trigger_score_based_disable(self):
        summary = summarise_skill_feedback(
            [
                {"signals": {"copied": True}},
                {"signals": {"copied": True}, "score": 0.1},
            ]
        )
        assert summary.score_samples == 1
        assert summary.should_disable is False

    def test_signal_breakdown_prefers_newest_execution(self):
        # Newest-first input: the recorded breakdown must show the most
        # recent value per signal key, matching the recency-weighted math.
        summary = summarise_skill_feedback([{"signals": {"rating": 5}}, {"signals": {"rating": 1}}])
        assert summary.evidence["signal_breakdown"] == {"rating": 5}


class TestRecencyDecay:
    def test_recent_positive_outweighs_equal_old_negative(self):
        # Newest-first ordering (as MemoryStore.get_skill_executions returns).
        recent_positive = summarise_skill_feedback(
            [
                {"signals": {"thumbs_up": True}},
                {"signals": {"thumbs_up": True}},
                {"signals": {"thumbs_down": True}},
                {"signals": {"thumbs_down": True}},
            ]
        )
        recent_negative = summarise_skill_feedback(
            [
                {"signals": {"thumbs_down": True}},
                {"signals": {"thumbs_down": True}},
                {"signals": {"thumbs_up": True}},
                {"signals": {"thumbs_up": True}},
            ]
        )
        # Same evidence, opposite ordering: recency decides the sign.
        assert recent_positive.signal_confidence > 0.0
        assert recent_negative.signal_confidence < 0.0
        assert recent_positive.signal_confidence == pytest.approx(
            -recent_negative.signal_confidence
        )

    def test_recent_positive_run_triggers_rewrite_despite_old_negative(self):
        summary = summarise_skill_feedback(
            [
                {"signals": {"thumbs_up": True}},
                {"signals": {"thumbs_up": True}},
                {"signals": {"thumbs_up": True}},
                {"signals": {"thumbs_down": True}},
            ]
        )
        assert summary.signal_confidence >= 0.5
        assert summary.should_rewrite is True
        assert summary.should_disable is False

    def test_scores_are_recency_weighted(self):
        recent_high = summarise_skill_feedback(
            [{"signals": {}, "score": 1.0}, {"signals": {}, "score": 0.0}]
        )
        recent_low = summarise_skill_feedback(
            [{"signals": {}, "score": 0.0}, {"signals": {}, "score": 1.0}]
        )
        assert recent_high.average_score == pytest.approx(1.0 / 1.85)
        assert recent_low.average_score == pytest.approx(0.85 / 1.85)
        assert recent_high.average_score > 0.5 > recent_low.average_score
