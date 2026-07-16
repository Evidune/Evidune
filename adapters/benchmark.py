"""Generic benchmark adapter contract and registry."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from adapters.corpus import (
    CorpusSource,
    CorpusTask,
    CorpusValidation,
    EvaluationCorpus,
    SkillTaskPairing,
    corpus_source_root,
    load_evaluation_corpus,
    source_checkout_path,
    validate_evaluation_corpus,
)
from skills.benchmark import (
    BenchmarkExecution,
    BenchmarkExecutor,
    BenchmarkObservation,
    PreparedTask,
    ResetResult,
)
from skills.governance import EvaluationResult


class BenchmarkAdapter(ABC):
    adapter_id = ""
    revision = "v1"

    @abstractmethod
    def prepare(self, corpus: EvaluationCorpus, task: CorpusTask, split: str, workspace: Path):
        raise NotImplementedError

    @abstractmethod
    async def execute(
        self,
        prepared: PreparedTask,
        skill_content: str,
        model_ref: dict[str, Any],
        trial: int,
        executor: BenchmarkExecutor,
    ) -> BenchmarkExecution:
        raise NotImplementedError

    @abstractmethod
    def collect(self, execution: BenchmarkExecution) -> list[BenchmarkObservation]:
        raise NotImplementedError

    @abstractmethod
    def evaluate(
        self,
        prepared: PreparedTask,
        execution: BenchmarkExecution,
        execution_id: int,
        evaluator_revision: str,
    ) -> list[EvaluationResult]:
        raise NotImplementedError

    @abstractmethod
    def reset(self, prepared: PreparedTask) -> ResetResult:
        raise NotImplementedError


class BenchmarkAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, BenchmarkAdapter] = {}

    def register(self, adapter: BenchmarkAdapter) -> None:
        if not adapter.adapter_id:
            raise ValueError("Benchmark adapter must declare adapter_id")
        self._adapters[adapter.adapter_id] = adapter

    def get(self, adapter_id: str) -> BenchmarkAdapter:
        if adapter_id not in self._adapters:
            raise ValueError(f"Unknown benchmark adapter: {adapter_id}")
        return self._adapters[adapter_id]

    def names(self) -> list[str]:
        return sorted(self._adapters)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


__all__ = [
    "BenchmarkAdapter",
    "BenchmarkAdapterRegistry",
    "BenchmarkExecution",
    "BenchmarkExecutor",
    "BenchmarkObservation",
    "CorpusSource",
    "CorpusTask",
    "CorpusValidation",
    "EvaluationCorpus",
    "PreparedTask",
    "ResetResult",
    "SkillTaskPairing",
    "corpus_source_root",
    "load_evaluation_corpus",
    "source_checkout_path",
    "validate_evaluation_corpus",
    "write_json",
]
