"""Tests for adapters/generic_csv.py."""

import json
from pathlib import Path

import pytest

from adapters.generic_csv import GenericCsvAdapter


@pytest.fixture
def adapter():
    return GenericCsvAdapter()


class TestGenericCsvAdapter:
    def test_load_csv(self, adapter: GenericCsvAdapter, tmp_path: Path):
        csv_path = tmp_path / "data.csv"
        csv_path.write_text("title,reads,upvotes,date\nArticle A,1200,45,2026-04-01\nArticle B,300,10,2026-04-02\n")

        snapshot = adapter.fetch({
            "file": str(csv_path),
            "title_field": "title",
            "metric_fields": ["reads", "upvotes"],
            "metadata_fields": ["date"],
        })

        assert len(snapshot.records) == 2
        assert snapshot.records[0].title == "Article A"
        assert snapshot.records[0].metrics["reads"] == 1200.0
        assert snapshot.records[0].metrics["upvotes"] == 45.0
        assert snapshot.records[0].metadata["date"] == "2026-04-01"

    def test_load_json_list(self, adapter: GenericCsvAdapter, tmp_path: Path):
        json_path = tmp_path / "data.json"
        data = [
            {"title": "Post 1", "reads": 500, "likes": 20},
            {"title": "Post 2", "reads": 1500, "likes": 80},
        ]
        json_path.write_text(json.dumps(data))

        snapshot = adapter.fetch({
            "file": str(json_path),
            "title_field": "title",
            "metric_fields": ["reads", "likes"],
        })

        assert len(snapshot.records) == 2
        assert snapshot.records[1].title == "Post 2"
        assert snapshot.records[1].metrics["reads"] == 1500.0

    def test_load_json_wrapped(self, adapter: GenericCsvAdapter, tmp_path: Path):
        json_path = tmp_path / "data.json"
        data = {"records": [{"title": "A", "views": 100}]}
        json_path.write_text(json.dumps(data))

        snapshot = adapter.fetch({
            "file": str(json_path),
            "title_field": "title",
            "metric_fields": ["views"],
        })

        assert len(snapshot.records) == 1
        assert snapshot.records[0].metrics["views"] == 100.0

    def test_missing_file_raises(self, adapter: GenericCsvAdapter):
        with pytest.raises(FileNotFoundError):
            adapter.fetch({"file": "/nonexistent/data.csv"})

    def test_missing_file_config_raises(self, adapter: GenericCsvAdapter):
        with pytest.raises(ValueError, match="requires 'file'"):
            adapter.fetch({})

    def test_non_numeric_metric_skipped(self, adapter: GenericCsvAdapter, tmp_path: Path):
        csv_path = tmp_path / "data.csv"
        csv_path.write_text("title,reads\nArticle,not_a_number\n")

        snapshot = adapter.fetch({
            "file": str(csv_path),
            "title_field": "title",
            "metric_fields": ["reads"],
        })

        assert len(snapshot.records) == 1
        assert "reads" not in snapshot.records[0].metrics
