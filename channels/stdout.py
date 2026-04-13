"""Terminal stdout notification channel."""

from __future__ import annotations

from channels.base import Channel, IterationReport


class StdoutChannel(Channel):
    """Print iteration reports to the terminal."""

    def send_report(self, report: IterationReport) -> bool:
        print("=" * 60)
        print(f"Aiflay Iteration Report — {report.domain}")
        print("=" * 60)
        print(report.summary_text())
        print("=" * 60)
        return True
