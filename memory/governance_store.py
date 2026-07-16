"""Composition root for execution-grounded governance persistence."""

from memory.evaluation_store import EvaluationStoreMixin
from memory.evidence_store import EvidenceStoreMixin
from memory.experiment_store import ExperimentStoreMixin
from memory.probe_store import ProbeStoreMixin


class GovernanceStoreMixin(
    EvaluationStoreMixin,
    EvidenceStoreMixin,
    ProbeStoreMixin,
    ExperimentStoreMixin,
):
    """Methods mixed into ``MemoryStore`` while preserving package boundaries."""
