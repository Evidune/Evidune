"""Tests for agent/signal_collector.py."""

from agent.signal_collector import Signal, aggregate, signals_from_dict


class TestAggregateBasics:
    def test_empty_signals(self):
        result = aggregate([])
        assert result.confidence == 0.0
        assert result.sample_count == 0
        assert result.has_strong_signal is False

    def test_thumbs_up(self):
        result = aggregate([Signal("thumbs_up", True)])
        assert result.confidence == 1.0
        assert result.has_strong_signal is True

    def test_thumbs_down(self):
        result = aggregate([Signal("thumbs_down", True)])
        assert result.confidence == -1.0
        assert result.has_strong_signal is True

    def test_thumbs_up_plus_copy_saturates(self):
        # 1.0 (thumbs) + 0.5 (copy) clamps at 1.0
        result = aggregate([Signal("thumbs_up", True), Signal("copied", True)])
        assert result.confidence == 1.0

    def test_copy_alone_is_partial(self):
        result = aggregate([Signal("copied", True)])
        assert result.confidence == 0.5
        assert result.has_strong_signal is False

    def test_negative_signals_combine(self):
        result = aggregate([Signal("regenerated", True), Signal("edited_request", True)])
        # -0.7 + -0.5 = -1.2 → clamped to -1.0
        assert result.confidence == -1.0

    def test_neutral_signals_dont_count(self):
        result = aggregate([Signal("topic_switch", True), Signal("silent", True)])
        assert result.confidence == 0.0
        assert result.sample_count == 0


class TestRating:
    def test_rating_5_is_max_positive(self):
        result = aggregate([Signal("rating", 5)])
        assert result.confidence == 1.0
        assert result.has_strong_signal is True

    def test_rating_1_is_max_negative(self):
        result = aggregate([Signal("rating", 1)])
        assert result.confidence == -1.0

    def test_rating_3_is_neutral(self):
        result = aggregate([Signal("rating", 3)])
        assert result.confidence == 0.0
        assert result.has_strong_signal is True  # still strong because explicit

    def test_rating_percentage_50_is_neutral(self):
        result = aggregate([Signal("rating", 50)])
        assert result.confidence == 0.0

    def test_rating_percentage_100_is_max(self):
        result = aggregate([Signal("rating", 100)])
        assert result.confidence == 1.0


class TestBreakdown:
    def test_breakdown_includes_all_observed(self):
        result = aggregate([Signal("thumbs_up", True), Signal("copied", True)])
        assert result.breakdown == {"thumbs_up": True, "copied": True}

    def test_false_signals_skipped(self):
        result = aggregate([Signal("copied", False)])
        assert result.confidence == 0.0
        assert "copied" not in result.breakdown

    def test_sample_count(self):
        result = aggregate(
            [
                Signal("copied", True),
                Signal("topic_switch", True),  # neutral, doesn't count
                Signal("thumbs_up", True),
            ]
        )
        assert result.sample_count == 2  # copied + thumbs_up


class TestSignalsFromDict:
    def test_round_trip(self):
        d = {"thumbs_up": True, "copied": True, "rating": 4}
        signals = signals_from_dict(d)
        assert len(signals) == 3
        types = {s.type for s in signals}
        assert types == {"thumbs_up", "copied", "rating"}

    def test_empty_dict(self):
        assert signals_from_dict({}) == []
