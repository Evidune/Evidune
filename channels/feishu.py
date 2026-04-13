"""Feishu (Lark) notification channel — webhook + interactive card."""

from __future__ import annotations

import json
from typing import Any

import httpx

from channels.base import Channel, IterationReport


class FeishuChannel(Channel):
    """Send iteration reports to Feishu via webhook or bot API.

    Args:
        webhook: Feishu incoming webhook URL.
    """

    def __init__(self, webhook: str | None = None, **kwargs: Any) -> None:
        if not webhook:
            raise ValueError("FeishuChannel requires a 'webhook' URL")
        self.webhook = webhook

    def send_report(self, report: IterationReport) -> bool:
        """Send report as a Feishu interactive card."""
        card = self._build_card(report)
        payload = {
            "msg_type": "interactive",
            "card": card,
        }
        try:
            resp = httpx.post(self.webhook, json=payload, timeout=10)
            resp.raise_for_status()
            body = resp.json()
            return body.get("code", -1) == 0 or body.get("StatusCode", -1) == 0
        except (httpx.HTTPError, json.JSONDecodeError):
            return False

    def _build_card(self, report: IterationReport) -> dict[str, Any]:
        """Build a Feishu interactive card message."""
        elements: list[dict[str, Any]] = []

        # Summary section
        elements.append({
            "tag": "markdown",
            "content": f"**{report.analysis.summary}**",
        })

        # Top performers table
        if report.analysis.top_performers:
            top_lines = ["**Top Performers:**"]
            for i, r in enumerate(report.analysis.top_performers[:5], 1):
                metrics_str = ", ".join(f"{k}={v}" for k, v in r.metrics.items())
                top_lines.append(f"{i}. {r.title} ({metrics_str})")
            elements.append({
                "tag": "markdown",
                "content": "\n".join(top_lines),
            })

        # Bottom performers
        if report.analysis.bottom_performers:
            bottom_lines = ["**Bottom Performers:**"]
            for r in report.analysis.bottom_performers:
                metrics_str = ", ".join(f"{k}={v}" for k, v in r.metrics.items())
                bottom_lines.append(f"- {r.title} ({metrics_str})")
            elements.append({
                "tag": "markdown",
                "content": "\n".join(bottom_lines),
            })

        # Divider
        elements.append({"tag": "hr"})

        # Reference doc changes
        changed = [u for u in report.updates if u.has_changes]
        if changed:
            change_lines = [f"Updated **{len(changed)}** reference doc(s):"]
            for u in changed:
                change_lines.append(f"- `{u.path}` ({u.strategy})")
            elements.append({
                "tag": "markdown",
                "content": "\n".join(change_lines),
            })

        # Patterns
        if report.analysis.patterns:
            pattern_lines = ["**Patterns Detected:**"]
            for p in report.analysis.patterns:
                pattern_lines.append(f"- {p}")
            elements.append({
                "tag": "markdown",
                "content": "\n".join(pattern_lines),
            })

        # Commit info
        if report.commit_sha:
            elements.append({
                "tag": "note",
                "elements": [{
                    "tag": "plain_text",
                    "content": f"Commit: {report.commit_sha[:8]}",
                }],
            })

        return {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"Aiflay Daily Review — {report.domain}",
                },
                "template": "blue",
            },
            "elements": elements,
        }
