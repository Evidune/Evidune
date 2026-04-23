"""Generic CSV/JSON metrics adapter."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.metrics import MetricRecord, MetricsAdapter, MetricsSnapshot, OutcomeObservation


class GenericCsvAdapter(MetricsAdapter):
    """Import metrics from CSV or JSON files.

    Config options:
        file: Path to CSV or JSON file (required).
        title_field: Column name for the content title (default: "title").
        metric_fields: List of column names to treat as numeric metrics.
        metadata_fields: Legacy alias for dimension_fields.
        entity_id_field: Column used as the normalized entity identifier.
        timestamp_field: Column used as the normalized observation timestamp.
        dimension_fields: List of fields to preserve as dimensions.
        skill_name_field: Optional field that tags observations to a skill.
        skill_version_field: Optional field that tags observations to a skill version.
        exemplar_field: Optional field to preserve as human-readable exemplar text.
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
        dimension_fields = config.get("dimension_fields", metadata_fields)
        entity_id_field = config.get("entity_id_field", title_field)
        timestamp_field = config.get("timestamp_field", "")
        skill_name_field = config.get("skill_name_field", "")
        skill_version_field = config.get("skill_version_field", "")
        exemplar_field = config.get("exemplar_field", title_field)

        if path.suffix == ".json":
            rows = self._load_json_rows(path)
        else:
            rows = self._load_csv_rows(path)

        return MetricsSnapshot(
            domain="generic",
            records=self._build_records(rows, title_field, metric_fields, metadata_fields),
            observations=self._build_observations(
                rows,
                entity_id_field=entity_id_field,
                timestamp_field=timestamp_field,
                metric_fields=metric_fields,
                dimension_fields=dimension_fields,
                skill_name_field=skill_name_field,
                skill_version_field=skill_version_field,
                exemplar_field=exemplar_field,
            ),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

    def _load_csv_rows(self, path: Path) -> list[dict[str, Any]]:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader)

    def _load_json_rows(self, path: Path) -> list[dict[str, Any]]:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            data = data.get("records", data.get("data", data.get("items", [])))

        if not isinstance(data, list):
            raise ValueError(
                "JSON file must contain a list of records (or a dict with 'records'/'data'/'items' key)"
            )
        return [item for item in data if isinstance(item, dict)]

    @staticmethod
    def _coerce_metrics(row: dict[str, Any], metric_fields: list[str]) -> dict[str, float]:
        metrics: dict[str, float] = {}
        for field in metric_fields:
            if field not in row:
                continue
            try:
                metrics[field] = float(row[field])
            except (ValueError, TypeError):
                continue
        return metrics

    def _build_records(
        self,
        rows: list[dict[str, Any]],
        title_field: str,
        metric_fields: list[str],
        metadata_fields: list[str],
    ) -> list[MetricRecord]:
        records: list[MetricRecord] = []
        for row in rows:
            records.append(
                MetricRecord(
                    title=str(row.get(title_field, "")),
                    metrics=self._coerce_metrics(row, metric_fields),
                    metadata={field: row[field] for field in metadata_fields if field in row},
                )
            )
        return records

    def _build_observations(
        self,
        rows: list[dict[str, Any]],
        *,
        entity_id_field: str,
        timestamp_field: str,
        metric_fields: list[str],
        dimension_fields: list[str],
        skill_name_field: str,
        skill_version_field: str,
        exemplar_field: str,
    ) -> list[OutcomeObservation]:
        observations: list[OutcomeObservation] = []
        for row in rows:
            entity_id = str(row.get(entity_id_field, "")).strip()
            if not entity_id:
                continue
            dimensions = {field: row[field] for field in dimension_fields if field in row}
            metadata = {}
            exemplar = str(row.get(exemplar_field, "")).strip()
            if exemplar:
                metadata["exemplar"] = exemplar
            observations.append(
                OutcomeObservation(
                    entity_id=entity_id,
                    timestamp=str(row.get(timestamp_field, "")).strip() if timestamp_field else "",
                    metrics=self._coerce_metrics(row, metric_fields),
                    dimensions=dimensions,
                    source="generic_csv",
                    skill_name=(
                        str(row.get(skill_name_field, "")).strip() if skill_name_field else ""
                    ),
                    skill_version=(
                        str(row.get(skill_version_field, "")).strip() if skill_version_field else ""
                    ),
                    metadata=metadata,
                )
            )
        return observations
