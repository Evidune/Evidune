"""Base channel interface and channel registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from core.analyzer import AnalysisResult
from core.updater import UpdateResult


@dataclass
class IterationReport:
    """Report generated after one iteration loop."""

    domain: str
    analysis: AnalysisResult
    updates: list[UpdateResult]
    commit_sha: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return any(u.has_changes for u in self.updates)

    def summary_text(self) -> str:
        """Generate a plain text summary of the report."""
        lines = [f"[{self.domain}] {self.analysis.summary}"]

        changed = [u for u in self.updates if u.has_changes]
        if changed:
            lines.append(f"Updated {len(changed)} reference doc(s):")
            for u in changed:
                lines.append(f"  - {u.path} ({u.strategy})")

        if self.commit_sha:
            lines.append(f"Commit: {self.commit_sha[:8]}")

        if self.analysis.patterns:
            lines.append("Patterns:")
            for p in self.analysis.patterns:
                lines.append(f"  - {p}")

        return "\n".join(lines)


class Channel(ABC):
    """Base class for notification channels."""

    @abstractmethod
    def send_report(self, report: IterationReport) -> bool:
        """Send an iteration report. Returns True on success."""
        ...


# Channel registry
_registry: dict[str, type[Channel]] = {}


def register_channel(name: str, channel_cls: type[Channel]) -> None:
    _registry[name] = channel_cls


def create_channel(name: str, **kwargs: Any) -> Channel:
    if name not in _registry:
        # Auto-import known channels
        if name == "stdout":
            from channels.stdout import StdoutChannel

            register_channel("stdout", StdoutChannel)
        elif name == "feishu":
            from channels.feishu import FeishuChannel

            register_channel("feishu", FeishuChannel)
        else:
            raise ValueError(f"Unknown channel '{name}'. Available: {list(_registry.keys())}")
    return _registry[name](**kwargs)
