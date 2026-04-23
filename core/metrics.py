"""MetricsAdapter base class and adapter registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetricRecord:
    """A single content item's performance metrics."""

    title: str
    metrics: dict[str, float | int]  # e.g. {"reads": 1200, "upvotes": 45}
    metadata: dict[str, Any] = field(
        default_factory=dict
    )  # e.g. {"date": "2026-04-10", "url": "..."}


@dataclass
class MetricsSnapshot:
    """A collection of metric records from a single fetch."""

    domain: str
    records: list[MetricRecord] = field(default_factory=list)
    observations: list[OutcomeObservation] = field(default_factory=list)
    fetched_at: str = ""


@dataclass
class OutcomeObservation:
    """One normalized business outcome observation."""

    entity_id: str
    timestamp: str
    metrics: dict[str, float | int]
    dimensions: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    skill_name: str = ""
    skill_version: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class MetricsAdapter(ABC):
    """Base class for platform-specific metrics adapters."""

    @abstractmethod
    def fetch(self, config: dict[str, Any]) -> MetricsSnapshot:
        """Fetch metrics from the platform.

        Args:
            config: Adapter-specific configuration from evidune.yaml.

        Returns:
            MetricsSnapshot containing all fetched records.
        """
        ...


# Adapter registry
_registry: dict[str, type[MetricsAdapter]] = {}


def register_adapter(name: str, adapter_cls: type[MetricsAdapter]) -> None:
    _registry[name] = adapter_cls


def get_adapter(name: str) -> MetricsAdapter:
    if name not in _registry:
        # Try auto-import
        if name == "generic_csv":
            from adapters.generic_csv import GenericCsvAdapter

            register_adapter("generic_csv", GenericCsvAdapter)
        else:
            raise ValueError(f"Unknown adapter '{name}'. Available: {list(_registry.keys())}")
    return _registry[name]()
