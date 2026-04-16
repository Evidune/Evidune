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
        assert summary.average_score == pytest.approx(0.85)
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

    def test_empty_evidence_allows_metric_only_rewrite_path(self):
        summary = summarise_skill_feedback([])
        assert summary.signal_samples == 0
        assert summary.average_score is None
        assert summary.should_rewrite is True
