"""Generic CSV/JSON metrics adapter."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.metrics import MetricRecord, MetricsAdapter, MetricsSnapshot


class GenericCsvAdapter(MetricsAdapter):
    """Import metrics from CSV or JSON files.

    Config options:
        file: Path to CSV or JSON file (required).
        title_field: Column name for the content title (default: "title").
        metric_fields: List of column names to treat as numeric metrics.
        metadata_fields: List of column names to include as metadata.
    """

    def fetch(self, config: dict[str, Any]) -> MetricsSnapshot:
        file_path = config.get("file")
        if not file_path:
            raise ValueError("generic_csv adapter requires 'file' in config")

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Metrics file not found: {path}")

        title_field = config.get("title_field", "title")
        metric_fields = config.get("metric_fields", [])
        metadata_fields = config.get("metadata_fields", [])

        if path.suffix == ".json":
            records = self._load_json(path, title_field, metric_fields, metadata_fields)
        else:
            records = self._load_csv(path, title_field, metric_fields, metadata_fields)

        return MetricsSnapshot(
            domain="generic",
            records=records,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

    def _load_csv(
        self,
        path: Path,
        title_field: str,
        metric_fields: list[str],
        metadata_fields: list[str],
    ) -> list[MetricRecord]:
        records = []
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                title = row.get(title_field, "")
                metrics = {}
                for mf in metric_fields:
                    if mf in row:
                        try:
                            metrics[mf] = float(row[mf])
                        except (ValueError, TypeError):
                            pass
                metadata = {mf: row[mf] for mf in metadata_fields if mf in row}
                records.append(MetricRecord(title=title, metrics=metrics, metadata=metadata))
        return records

    def _load_json(
        self,
        path: Path,
        title_field: str,
        metric_fields: list[str],
        metadata_fields: list[str],
    ) -> list[MetricRecord]:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            data = data.get("records", data.get("data", data.get("items", [])))

        if not isinstance(data, list):
            raise ValueError("JSON file must contain a list of records (or a dict with 'records'/'data'/'items' key)")

        records = []
        for item in data:
            title = item.get(title_field, "")
            metrics = {}
            for mf in metric_fields:
                if mf in item:
                    try:
                        metrics[mf] = float(item[mf])
                    except (ValueError, TypeError):
                        pass
            metadata = {mf: item[mf] for mf in metadata_fields if mf in item}
            records.append(MetricRecord(title=title, metrics=metrics, metadata=metadata))
        return records
