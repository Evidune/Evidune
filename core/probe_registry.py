"""Code-owned registries for read-only probes and deterministic evaluators."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any

from skills.governance import EvaluationResult

ProbeHandler = Callable[..., Awaitable[dict[str, Any]] | dict[str, Any]]
EvaluatorHandler = Callable[
    [dict[str, Any], dict[str, Any]],
    list[EvaluationResult] | EvaluationResult,
]


@dataclass(frozen=True)
class ProbeDefinition:
    probe_id: str
    revision: str
    handler: ProbeHandler
    allowed_arguments: set[str] = field(default_factory=set)
    capability: str = "read"
    timeout_s: float = 10.0
    max_response_bytes: int = 200_000
    output_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluatorDefinition:
    evaluator_id: str
    revision: str
    evaluator_type: str
    handler: EvaluatorHandler


class ProbeRegistry:
    """Allowlisted probe registry without shell, Python, or write tools."""

    def __init__(self) -> None:
        self._definitions: dict[str, ProbeDefinition] = {}

    def register(self, definition: ProbeDefinition) -> None:
        if definition.capability != "read":
            raise ValueError("Background probes must be read-only")
        if not definition.probe_id or not definition.revision:
            raise ValueError("Probe requires id and revision")
        if definition.probe_id in self._definitions:
            raise ValueError(f"Probe is already registered: {definition.probe_id}")
        self._definitions[definition.probe_id] = definition

    async def execute(self, probe_id: str, arguments: dict[str, Any]) -> tuple[dict, str]:
        definition = self._definitions.get(probe_id)
        if definition is None:
            raise ValueError(f"Probe is not allowlisted: {probe_id}")
        unexpected = sorted(set(arguments) - definition.allowed_arguments)
        if unexpected:
            raise ValueError(f"Probe arguments are not allowlisted: {', '.join(unexpected)}")

        async def invoke() -> dict[str, Any]:
            result = definition.handler(**arguments)
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, dict):
                raise ValueError("Probe output must be a mapping")
            return result

        payload = await asyncio.wait_for(invoke(), timeout=definition.timeout_s)
        encoded = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        if len(encoded) > definition.max_response_bytes:
            raise ValueError("Probe output exceeds the configured response limit")
        _validate_schema(payload, definition.output_schema)
        return payload, definition.revision


class EvaluatorRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, EvaluatorDefinition] = {}

    def register(self, definition: EvaluatorDefinition) -> None:
        if not definition.evaluator_id or not definition.revision:
            raise ValueError("Evaluator requires id and revision")
        if definition.evaluator_id in self._definitions:
            raise ValueError(f"Evaluator is already registered: {definition.evaluator_id}")
        self._definitions[definition.evaluator_id] = definition

    def evaluate(
        self,
        evaluator_id: str,
        binding: dict[str, Any],
        payload: dict[str, Any],
        expected_revision: str = "",
    ) -> list[EvaluationResult]:
        definition = self._definitions.get(evaluator_id)
        if definition is None:
            raise ValueError(f"Evaluator is not allowlisted: {evaluator_id}")
        if expected_revision and definition.revision != expected_revision:
            raise ValueError(
                f"Evaluator revision mismatch: plan={expected_revision}, "
                f"runtime={definition.revision}"
            )
        results = definition.handler(binding, payload)
        if isinstance(results, EvaluationResult):
            results = [results]
        if not results:
            raise ValueError("Evaluator returned no result")
        return [
            replace(
                result,
                execution_id=int(binding["execution_id"]),
                skill_name=str(binding["skill_name"]),
                skill_version=str(binding.get("skill_version") or ""),
                evaluator_id=definition.evaluator_id,
                evaluator_revision=definition.revision,
                evaluator_type=definition.evaluator_type,
                contract_digest=str(binding.get("contract_digest") or ""),
            )
            for result in results
        ]


def _validate_schema(payload: dict[str, Any], schema: dict[str, Any]) -> None:
    if not schema:
        return
    missing = [str(key) for key in schema.get("required") or [] if key not in payload]
    if missing:
        raise ValueError(f"Probe output misses required fields: {', '.join(missing)}")
    type_map = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    for key, spec in (schema.get("properties") or {}).items():
        expected = type_map.get(spec.get("type")) if isinstance(spec, dict) else None
        if key in payload and expected:
            numeric_bool = spec.get("type") in {"number", "integer"} and isinstance(
                payload[key], bool
            )
            if numeric_bool or not isinstance(payload[key], expected):
                raise ValueError(f"Probe output field {key} has the wrong type")
