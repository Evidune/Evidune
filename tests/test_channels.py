"""Tests for channels."""

import json
from unittest.mock import patch, MagicMock

import pytest

from channels.base import IterationReport
from channels.stdout import StdoutChannel
from channels.feishu import FeishuChannel
from core.analyzer import AnalysisResult
from core.metrics import MetricRecord
from core.updater import UpdateResult


def _make_report() -> IterationReport:
    analysis = AnalysisResult(
        domain="test",
        total_records=10,
        top_performers=[
            MetricRecord(title="Best Article", metrics={"reads": 5000, "upvotes": 200}),
        ],
        bottom_performers=[
            MetricRecord(title="Worst Article", metrics={"reads": 50, "upvotes": 1}),
        ],
        patterns=["Top performers have longer titles (avg 25 vs 15 chars)"],
        summary="test: 10 items, total reads=15000, avg=1500.",
    )
    updates = [
        UpdateResult(path="refs/case-studies.md", strategy="replace_section", has_changes=True, old_content="old", new_content="new"),
        UpdateResult(path="refs/hot.md", strategy="full_replace", has_changes=False, old_content="same", new_content="same"),
    ]
    return IterationReport(
        domain="test",
        analysis=analysis,
        updates=updates,
        commit_sha="abc12345def67890",
    )


class TestStdoutChannel:
    def test_send_report(self, capsys):
        channel = StdoutChannel()
        result = channel.send_report(_make_report())
        assert result is True
        captured = capsys.readouterr()
        assert "test" in captured.out
        assert "case-studies.md" in captured.out

    def test_report_summary_text(self):
        report = _make_report()
        text = report.summary_text()
        assert "test" in text
        assert "refs/case-studies.md" in text
        assert "abc12345" in text


class TestFeishuChannel:
    def test_requires_webhook(self):
        with pytest.raises(ValueError, match="webhook"):
            FeishuChannel()

    def test_build_card(self):
        channel = FeishuChannel(webhook="https://example.com/hook")
        report = _make_report()
        card = channel._build_card(report)
        assert card["header"]["title"]["content"] == "Aiflay Daily Review — test"
        assert len(card["elements"]) > 0

    @patch("channels.feishu.httpx.post")
    def test_send_report_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        channel = FeishuChannel(webhook="https://example.com/hook")
        result = channel.send_report(_make_report())
        assert result is True
        mock_post.assert_called_once()

    @patch("channels.feishu.httpx.post")
    def test_send_report_failure(self, mock_post):
        import httpx
        mock_post.side_effect = httpx.HTTPError("connection failed")

        channel = FeishuChannel(webhook="https://example.com/hook")
        result = channel.send_report(_make_report())
        assert result is False
