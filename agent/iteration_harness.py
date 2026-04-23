"""Shared deterministic skill governance workflow built on harness records."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.harness.models import BudgetSummary, DecisionRecord, HarnessTask, TaskBrief
from agent.harness.profiles import get_squad_profile
from agent.skill_feedback import SkillFeedbackSummary, summarise_skill_feedback

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_MANAGED_ADJUSTMENTS_HEADING = "### Outcome-Backed Adjustments"


@dataclass
class UpdateResult:
    path: str
    strategy: str
    has_changes: bool
    old_content: str
    new_content: str


@dataclass
class IterationDecisionPacket:
    skill_name: str
    skill_path: str
    skill_origin: str
    update_section: str = "## Reference Data"
    current_content: str = ""
    metrics_summary: dict[str, Any] = field(default_factory=dict)
    outcome_summary: dict[str, Any] | None = None
    regression_summary: dict[str, Any] = field(default_factory=dict)
    exemplar_slice: list[dict[str, Any]] = field(default_factory=list)
    executions: list[dict[str, Any]] = field(default_factory=list)
    execution_contract: dict[str, Any] | None = None
    execution_evaluations: list[dict[str, Any]] = field(default_factory=list)
    outcome_contract: dict[str, Any] | None = None
    outcome_summaries: list[dict[str, Any]] = field(default_factory=list)
    feedback: SkillFeedbackSummary | None = None
    lifecycle_history: list[dict[str, Any]] = field(default_factory=list)
    surface: str = "run"
    conversation_id: str = ""
    task_kind: str = "skill_iteration"


@dataclass
class IterationDecision:
    decision: str
    skill_status: str
    update: UpdateResult
    task: HarnessTask


def replace_section(content: str, heading: str, new_section: str) -> str:
    """Replace a markdown section by heading, appending if missing."""
    target = heading.lstrip("#").strip()
    lines = content.split("\n")
    start_idx: int | None = None
    start_level: int | None = None

    for idx, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        if start_idx is None and title == target:
            start_idx = idx
            start_level = level
        elif start_idx is not None and level <= (start_level or 0):
            replaced = lines[:start_idx] + new_section.rstrip().split("\n") + lines[idx:]
            return "\n".join(replaced).rstrip() + "\n"

    if start_idx is not None:
        replaced = lines[:start_idx] + new_section.rstrip().split("\n")
        return "\n".join(replaced).rstrip() + "\n"

    suffix = "" if not content.strip() else "\n\n"
    return content.rstrip() + suffix + new_section.rstrip() + "\n"


def _extract_section(body: str, heading: str) -> str | None:
    target = heading.lstrip("#").strip()
    lines = body.split("\n")
    start_idx: int | None = None
    start_level: int | None = None

    for idx, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        if start_idx is None and title == target:
            start_idx = idx
            start_level = level
        elif start_idx is not None and level <= (start_level or 0):
            return "\n".join(lines[start_idx + 1 : idx]).strip()

    if start_idx is not None:
        return "\n".join(lines[start_idx + 1 :]).strip()
    return None


def _strip_managed_adjustments(instructions_body: str) -> str:
    lines = instructions_body.rstrip().split("\n")
    output: list[str] = []
    skipping = False

    for line in lines:
        if line.strip() == _MANAGED_ADJUSTMENTS_HEADING:
            skipping = True
            continue
        if skipping and line.startswith("## "):
            skipping = False
        if not skipping:
            output.append(line)

    return "\n".join(output).rstrip()


def _build_rewritten_instructions(
    instructions_body: str,
    *,
    outcome_contract: dict[str, Any],
    outcome_summary: dict[str, Any] | None,
    regression_summary: dict[str, Any],
    exemplar_slice: list[dict[str, Any]],
    feedback: SkillFeedbackSummary,
) -> str:
    base = _strip_managed_adjustments(instructions_body).rstrip()
    primary_kpi = outcome_contract.get("primary_kpi") or "the primary KPI"
    lines = ["## Instructions", ""]
    if base:
        lines.append(base)
        lines.append("")

    lines.append(_MANAGED_ADJUSTMENTS_HEADING)
    lines.append("")
    if outcome_summary:
        current_value = outcome_summary.get("current_value")
        baseline_value = outcome_summary.get("baseline_value")
        delta = outcome_summary.get("delta")
        if current_value is not None and baseline_value is not None and delta is not None:
            lines.append(
                f"- Optimize for `{primary_kpi}` using the latest window evidence: "
                f"current={current_value:.3f}, baseline={baseline_value:.3f}, delta={delta:.3f}."
            )
    if regression_summary.get("rewrite_candidate"):
        lines.append(
            f"- Prioritize changes that improve `{primary_kpi}` in the weakest current segment."
        )
    for pattern in regression_summary.get("legacy_patterns", [])[:3]:
        lines.append(f"- Reinforce this observed pattern: {pattern}.")
    for segment in (outcome_summary or {}).get("segment_breakdown", [])[:2]:
        label = ", ".join(f"{k}={v}" for k, v in segment.get("segment", {}).items())
        lines.append(
            f"- Address the regression in segment `{label or 'default'}` "
            f"(value={segment['value']:.3f}, samples={segment['sample_count']})."
        )
    for exemplar in exemplar_slice[:2]:
        label = exemplar.get("exemplar") or exemplar.get("entity_id") or "example"
        value = exemplar.get(primary_kpi)
        if value is not None:
            lines.append(f"- Learn from exemplar `{label}` ({primary_kpi}={value:.3f}).")
    lines.append(
        "- Keep the manual guidance above intact; treat these adjustments as evidence-backed overrides only."
    )
    if feedback.average_score is not None:
        lines.append(
            f"- Recent execution evaluator average is {feedback.average_score:.2f}; do not trade execution quality for KPI gains."
        )
    return "\n".join(lines)


def build_decision_packet(
    memory,
    *,
    skill,
    current: str,
    feedback: SkillFeedbackSummary | None = None,
    result=None,
    surface: str = "run",
    conversation_id: str = "",
    task_kind: str = "skill_iteration",
) -> IterationDecisionPacket:
    """Build the unified evidence packet used by run, serve, and web feedback."""

    state = memory.get_skill_state(skill.name)
    emerged = memory.get_emerged_skill(skill.name)
    origin = state["origin"] if state else ("emerged" if emerged else "base")
    executions = memory.get_skill_executions(skill.name, limit=20)
    execution_contract_row = memory.get_skill_evaluation_contract(skill.name)
    execution_contract = execution_contract_row.get("contract") if execution_contract_row else None
    execution_evaluations = memory.list_skill_evaluations(skill.name, limit=20)
    outcome_summaries = memory.list_outcome_window_summaries(skill.name, limit=20)

    outcome_summary = getattr(result, "outcome_summary", None) if result is not None else None
    regression_summary = dict(getattr(result, "regression_summary", {}) or {})
    exemplar_slice = list(getattr(result, "exemplar_slice", []) or [])
    metrics_summary = dict(getattr(result, "raw_stats", {}) or {})
    if result is not None and not exemplar_slice:
        top_performers = list(getattr(result, "top_performers", []) or [])
        exemplar_slice = [
            {
                "entity_id": getattr(item, "title", ""),
                "exemplar": getattr(item, "title", ""),
                **dict(getattr(item, "metrics", {}) or {}),
            }
            for item in top_performers
        ]
        if top_performers:
            metrics_summary.setdefault(
                "top_titles",
                [getattr(item, "title", "") for item in top_performers],
            )
    if result is not None and "legacy_patterns" not in regression_summary:
        patterns = list(getattr(result, "patterns", []) or [])
        if patterns:
            regression_summary["legacy_patterns"] = patterns

    return IterationDecisionPacket(
        skill_name=skill.name,
        skill_path=str(skill.path),
        skill_origin=origin,
        update_section=skill.update_section,
        current_content=current,
        metrics_summary=metrics_summary,
        outcome_summary=(
            {
                "window": outcome_summary.window,
                "sample_count": outcome_summary.sample_count,
                "baseline_value": outcome_summary.baseline_value,
                "current_value": outcome_summary.current_value,
                "delta": outcome_summary.delta,
                "confidence": outcome_summary.confidence,
                "segment_breakdown": outcome_summary.segment_breakdown,
                "policy_state": outcome_summary.policy_state,
            }
            if outcome_summary is not None
            else None
        ),
        regression_summary=regression_summary,
        exemplar_slice=exemplar_slice,
        executions=executions,
        execution_contract=(
            skill.execution_contract.to_dict() if skill.execution_contract else execution_contract
        ),
        execution_evaluations=execution_evaluations,
        outcome_contract=skill.outcome_contract.to_dict() if skill.outcome_contract else None,
        outcome_summaries=outcome_summaries,
        feedback=feedback or summarise_skill_feedback(executions),
        lifecycle_history=memory.list_skill_lifecycle_events(skill.name, limit=20),
        surface=surface,
        conversation_id=conversation_id,
        task_kind=task_kind,
    )


class IterationHarness:
    """Deterministic workflow for keep/rewrite/rollback/disable decisions."""

    def __init__(self, memory) -> None:
        self.memory = memory

    def run(self, *, packet: IterationDecisionPacket) -> IterationDecision:
        task = HarnessTask(
            id=f"iter-{packet.skill_name}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
            conversation_id=packet.conversation_id,
            surface=packet.surface,
            squad=get_squad_profile("iteration"),
            brief=TaskBrief(
                user_input=f"Govern skill {packet.skill_name}",
                conversation_id=packet.conversation_id,
                selected_skills=[packet.skill_name],
            ),
            budget_summary=BudgetSummary(max_rounds=1),
        )
        self.memory.create_harness_task(
            task_id=task.id,
            conversation_id=packet.conversation_id,
            surface=packet.surface,
            squad_profile="iteration",
            status="running",
            task_kind=packet.task_kind,
            user_input=packet.skill_name,
            selected_skills=[packet.skill_name],
            role_roster=task.squad.roles,
            budget=task.budget_summary.to_dict(),
        )

        evidence = self._evidence_payload(packet)
        evidence_step = self.memory.record_harness_step(
            task.id,
            phase="collect",
            role="evidence_collector",
            status="completed",
            summary=self._summarise(f"Collected evidence for {packet.skill_name}"),
            tool_trace=[],
            budget=task.budget_summary.to_dict(),
        )
        self.memory.record_harness_artifact(
            task.id,
            step_id=evidence_step,
            phase="collect",
            role="evidence_collector",
            kind="evidence",
            summary=self._summarise(str(evidence)),
            content=str(evidence),
            accepted=True,
            meta=evidence,
        )

        decision, proposed_content = self._propose(packet)
        proposer_step = self.memory.record_harness_step(
            task.id,
            phase="propose",
            role="rewrite_proposer",
            status="completed",
            summary=self._summarise(f"Proposal decision: {decision}"),
            tool_trace=[],
            budget=task.budget_summary.to_dict(),
        )
        self.memory.record_harness_artifact(
            task.id,
            step_id=proposer_step,
            phase="propose",
            role="rewrite_proposer",
            kind="proposal",
            summary=self._summarise(proposed_content or decision),
            content=proposed_content or decision,
            accepted=decision == "keep",
            meta={"decision": decision},
        )

        reviewer_ok = decision in {"keep", "disable"} or bool(
            proposed_content and proposed_content != packet.current_content
        )
        reviewer_step = self.memory.record_harness_step(
            task.id,
            phase="review",
            role="safety_reviewer",
            status="completed",
            summary=self._summarise("Approved proposal" if reviewer_ok else "Rejected proposal"),
            tool_trace=[],
            budget=task.budget_summary.to_dict(),
        )
        self.memory.record_harness_artifact(
            task.id,
            step_id=reviewer_step,
            phase="review",
            role="safety_reviewer",
            kind="review",
            summary=self._summarise("approved" if reviewer_ok else "rejected"),
            content="approved" if reviewer_ok else "rejected",
            accepted=reviewer_ok,
            meta={"decision": decision},
        )
        if decision in {"rewrite", "rollback", "refresh"} and not reviewer_ok:
            decision = "keep"
            proposed_content = ""

        arbiter_step = self.memory.record_harness_step(
            task.id,
            phase="decide",
            role="lifecycle_arbiter",
            status="completed",
            summary=self._summarise(f"Lifecycle decision: {decision}"),
            tool_trace=[],
            budget=task.budget_summary.to_dict(),
        )

        update, skill_status = self._apply_decision(
            packet=packet,
            decision=decision,
            proposed_content=proposed_content,
            harness_task_id=task.id,
        )
        task.decision = DecisionRecord(decision=decision, rationale=f"Decision={decision}")
        task.status = "completed"
        task.final_output = decision
        task.convergence_summary = {"decision": decision, "skill_status": skill_status}
        self.memory.record_harness_artifact(
            task.id,
            step_id=arbiter_step,
            phase="decide",
            role="lifecycle_arbiter",
            kind="decision",
            summary=self._summarise(decision),
            content=decision,
            accepted=True,
            meta={
                "path": update.path,
                "has_changes": update.has_changes,
                "skill_status": skill_status,
            },
        )
        self.memory.update_harness_task(
            task.id,
            status="completed",
            summary=self._summarise(decision),
            convergence=task.convergence_summary,
            final_output=decision,
            budget=task.budget_summary.to_dict(),
        )
        return IterationDecision(
            decision=decision,
            skill_status=skill_status,
            update=update,
            task=task,
        )

    def _propose(self, packet: IterationDecisionPacket) -> tuple[str, str]:
        feedback = packet.feedback or summarise_skill_feedback(packet.executions)
        latest_rewrite = self._latest_rewrite_event(packet.lifecycle_history)
        reference_content = self._build_reference_content(packet)
        execution_decision = self._execution_contract_decision(packet)
        outcome_policy = (packet.outcome_summary or {}).get("policy_state", {})

        if (
            outcome_policy.get("rollback_candidate")
            and latest_rewrite
            and latest_rewrite.get("content_before")
        ):
            return (
                "rollback",
                self._build_rollback_content(
                    restored=latest_rewrite["content_before"],
                    section=packet.update_section,
                    reference_content=reference_content,
                ),
            )

        if execution_decision == "disable" or (
            feedback.should_disable
            and not (packet.execution_evaluations and feedback.signal_samples == 0)
        ):
            if latest_rewrite and latest_rewrite.get("content_before"):
                return (
                    "rollback",
                    self._build_rollback_content(
                        restored=latest_rewrite["content_before"],
                        section=packet.update_section,
                        reference_content=reference_content,
                    ),
                )
            return "disable", ""

        if outcome_policy.get("severe_regression") and not latest_rewrite:
            return "disable", ""

        if outcome_policy.get("rewrite_candidate"):
            proposed = self._build_rewrite_content(
                current=packet.current_content,
                packet=packet,
                reference_content=reference_content,
            )
            if proposed:
                return "rewrite", proposed

        if packet.exemplar_slice and feedback.should_rewrite:
            proposed = self._build_rewrite_content(
                current=packet.current_content,
                packet=packet,
                reference_content=reference_content,
            )
            if proposed:
                return "rewrite", proposed

        if execution_decision == "rewrite":
            proposed = self._build_rewrite_content(
                current=packet.current_content,
                packet=packet,
                reference_content=reference_content,
            )
            if proposed:
                return "rewrite", proposed

        if packet.surface == "run" and reference_content.strip():
            refreshed = self._build_refresh_content(
                current=packet.current_content,
                section=packet.update_section,
                reference_content=reference_content,
            )
            if refreshed and refreshed != packet.current_content:
                return "refresh", refreshed

        return "keep", ""

    def _apply_decision(
        self,
        *,
        packet: IterationDecisionPacket,
        decision: str,
        proposed_content: str,
        harness_task_id: str,
    ) -> tuple[UpdateResult, str]:
        evidence = self._lifecycle_evidence(packet)
        current_status = self.memory.resolve_skill_status(packet.skill_name)

        if decision == "rewrite" and proposed_content:
            Path(packet.skill_path).write_text(proposed_content, encoding="utf-8")
            self.memory.upsert_skill_state(
                packet.skill_name,
                origin=packet.skill_origin,
                path=packet.skill_path,
                status="active",
                reason="Outcome-driven rewrite approved by iteration harness",
                evidence=evidence,
            )
            self.memory.record_skill_lifecycle_event(
                packet.skill_name,
                "rewrite",
                status="active",
                path=packet.skill_path,
                harness_task_id=harness_task_id,
                reason="Outcome-driven rewrite approved by iteration harness",
                evidence=evidence,
                content_before=packet.current_content,
                content_after=proposed_content,
            )
            return (
                UpdateResult(
                    path=packet.skill_path,
                    strategy="skill_rewrite",
                    has_changes=True,
                    old_content=packet.current_content,
                    new_content=proposed_content,
                ),
                "active",
            )

        if decision == "rollback" and proposed_content:
            Path(packet.skill_path).write_text(proposed_content, encoding="utf-8")
            self.memory.set_skill_state(
                packet.skill_name,
                "rolled_back",
                origin=packet.skill_origin,
                path=packet.skill_path,
                reason="Iteration harness rolled back the last automatic rewrite",
                evidence=evidence,
            )
            self.memory.record_skill_lifecycle_event(
                packet.skill_name,
                "rollback",
                status="rolled_back",
                path=packet.skill_path,
                harness_task_id=harness_task_id,
                reason="Iteration harness rolled back the last automatic rewrite",
                evidence=evidence,
                content_before=packet.current_content,
                content_after=proposed_content,
            )
            return (
                UpdateResult(
                    path=packet.skill_path,
                    strategy="skill_rollback",
                    has_changes=True,
                    old_content=packet.current_content,
                    new_content=proposed_content,
                ),
                "rolled_back",
            )

        if decision == "disable":
            self.memory.set_skill_state(
                packet.skill_name,
                "disabled",
                origin=packet.skill_origin,
                path=packet.skill_path,
                reason="Iteration harness disabled the skill after negative evidence",
                evidence=evidence,
            )
            self.memory.record_skill_lifecycle_event(
                packet.skill_name,
                "disable",
                status="disabled",
                path=packet.skill_path,
                harness_task_id=harness_task_id,
                reason="Iteration harness disabled the skill",
                evidence=evidence,
                content_before=packet.current_content,
                content_after=packet.current_content,
            )
            return (
                UpdateResult(
                    path=packet.skill_path,
                    strategy="skill_disable",
                    has_changes=False,
                    old_content=packet.current_content,
                    new_content=packet.current_content,
                ),
                "disabled",
            )

        if decision == "refresh" and proposed_content:
            Path(packet.skill_path).write_text(proposed_content, encoding="utf-8")
            self.memory.upsert_skill_state(
                packet.skill_name,
                origin=packet.skill_origin,
                path=packet.skill_path,
                status="active",
                reason="Outcome evidence refreshed without changing instructions",
                evidence=evidence,
            )
            self.memory.record_skill_lifecycle_event(
                packet.skill_name,
                "refresh",
                status="active",
                path=packet.skill_path,
                harness_task_id=harness_task_id,
                reason="Outcome evidence refreshed without changing instructions",
                evidence=evidence,
                content_before=packet.current_content,
                content_after=proposed_content,
            )
            return (
                UpdateResult(
                    path=packet.skill_path,
                    strategy="skill_reference_refresh",
                    has_changes=True,
                    old_content=packet.current_content,
                    new_content=proposed_content,
                ),
                "active",
            )

        return (
            UpdateResult(
                path=packet.skill_path,
                strategy="skill_keep",
                has_changes=False,
                old_content=packet.current_content,
                new_content=packet.current_content,
            ),
            current_status,
        )

    @staticmethod
    def _latest_rewrite_event(history: list[dict[str, Any]]) -> dict[str, Any] | None:
        for event in history:
            if event.get("action") == "rewrite":
                return event
        return None

    @staticmethod
    def _execution_contract_evidence_summary(packet: IterationDecisionPacket) -> dict[str, Any]:
        scores = [
            float(item["aggregate_score"])
            for item in packet.execution_evaluations
            if item.get("aggregate_score") is not None
        ]
        criteria_totals: dict[str, list[float]] = {}
        for item in packet.execution_evaluations:
            for name, score in (item.get("criteria_scores") or {}).items():
                try:
                    criteria_totals.setdefault(name, []).append(float(score))
                except (TypeError, ValueError):
                    continue
        criteria_averages = {
            name: sum(values) / len(values) for name, values in criteria_totals.items() if values
        }
        lowest = sorted(criteria_averages, key=criteria_averages.get)[:3]
        return {
            "samples": len(scores),
            "average_score": (sum(scores) / len(scores)) if scores else None,
            "criteria_averages": criteria_averages,
            "lowest_criteria": lowest,
        }

    def _execution_contract_decision(self, packet: IterationDecisionPacket) -> str:
        contract = packet.execution_contract or {}
        summary = self._execution_contract_evidence_summary(packet)
        average = summary["average_score"]
        samples = summary["samples"]
        if average is None or samples <= 0:
            return "keep"

        disable_below = float(contract.get("disable_below_score", 0.25) or 0.25)
        rewrite_below = float(contract.get("rewrite_below_score", 0.55) or 0.55)
        min_disable = int(contract.get("min_samples_for_disable", 2) or 2)
        min_rewrite = int(contract.get("min_samples_for_rewrite", 3) or 3)

        if samples >= min_disable and average <= disable_below:
            return "disable"
        if samples >= min_rewrite and average <= rewrite_below:
            return "rewrite"
        return "keep"

    @staticmethod
    def _summarise(text: str, limit: int = 160) -> str:
        compact = " ".join((text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"

    @staticmethod
    def _split_frontmatter(content: str) -> tuple[str, str]:
        match = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL).match(content)
        if not match:
            return "", content
        return match.group(0), content[match.end() :].lstrip("\n")

    def _build_rewrite_content(
        self,
        *,
        current: str,
        packet: IterationDecisionPacket,
        reference_content: str,
    ) -> str:
        prefix, body = self._split_frontmatter(current)
        instructions_body = _extract_section(body, "## Instructions")
        if instructions_body is None:
            return ""
        rewritten = _build_rewritten_instructions(
            instructions_body,
            outcome_contract=packet.outcome_contract or {},
            outcome_summary=packet.outcome_summary,
            regression_summary=packet.regression_summary,
            exemplar_slice=packet.exemplar_slice,
            feedback=packet.feedback or summarise_skill_feedback(packet.executions),
        )
        new_body = replace_section(body, "## Instructions", rewritten)
        if reference_content.strip():
            new_body = replace_section(new_body, packet.update_section, reference_content)
        return (prefix + new_body.rstrip() + "\n") if prefix else (new_body.rstrip() + "\n")

    def _build_rollback_content(
        self,
        *,
        restored: str,
        section: str,
        reference_content: str,
    ) -> str:
        if not reference_content.strip():
            return restored
        prefix, body = self._split_frontmatter(restored)
        updated_body = replace_section(body, section, reference_content)
        return (prefix + updated_body.rstrip() + "\n") if prefix else (updated_body.rstrip() + "\n")

    def _build_refresh_content(
        self,
        *,
        current: str,
        section: str,
        reference_content: str,
    ) -> str:
        prefix, body = self._split_frontmatter(current)
        updated_body = replace_section(body, section, reference_content)
        return (prefix + updated_body.rstrip() + "\n") if prefix else (updated_body.rstrip() + "\n")

    def _build_reference_content(self, packet: IterationDecisionPacket) -> str:
        legacy_patterns = packet.regression_summary.get("legacy_patterns", [])
        if (
            not packet.outcome_summary
            and not packet.execution_evaluations
            and not packet.exemplar_slice
            and not legacy_patterns
        ):
            return ""
        lines = [packet.update_section, ""]
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"*Auto-updated by evidune on {timestamp}*")
        lines.append("")

        primary_kpi = (packet.outcome_contract or {}).get("primary_kpi")
        if packet.outcome_summary and primary_kpi:
            lines.append(f"### KPI Window: {primary_kpi}")
            lines.append(f"- Sample size: {packet.outcome_summary.get('sample_count', 0)}")
            current_value = packet.outcome_summary.get("current_value")
            baseline_value = packet.outcome_summary.get("baseline_value")
            delta = packet.outcome_summary.get("delta")
            if current_value is not None:
                lines.append(f"- Current value: {current_value:.3f}")
            if baseline_value is not None:
                lines.append(f"- Baseline value: {baseline_value:.3f}")
            if delta is not None:
                lines.append(f"- Delta: {delta:.3f}")
            lines.append(f"- Confidence: {packet.outcome_summary.get('confidence', 0.0):.2f}")
            active = [
                key
                for key, value in (packet.outcome_summary.get("policy_state") or {}).items()
                if value is True
            ]
            lines.append(f"- Policy state: {', '.join(active) if active else 'stable'}")
            lines.append("")

        segments = (packet.outcome_summary or {}).get("segment_breakdown") or []
        if segments:
            lines.append("### Segment Breakdown")
            for segment in segments:
                label = ", ".join(f"{k}={v}" for k, v in segment.get("segment", {}).items())
                lines.append(
                    f"- {label or '(unlabeled)'}: value={segment['value']:.3f}, "
                    f"samples={segment['sample_count']}"
                )
            lines.append("")

        if packet.exemplar_slice:
            lines.append("### Exemplars")
            for exemplar in packet.exemplar_slice:
                label = exemplar.get("exemplar") or exemplar.get("entity_id") or "example"
                value = exemplar.get(primary_kpi, None) if primary_kpi else None
                if value is None:
                    lines.append(f"- {label}")
                else:
                    lines.append(f"- {label} ({primary_kpi}={value:.3f})")
            lines.append("")

        if legacy_patterns:
            lines.append("### Observed Patterns")
            for pattern in legacy_patterns[:3]:
                lines.append(f"- {pattern}")
            lines.append("")

        if packet.execution_evaluations:
            summary = self._execution_contract_evidence_summary(packet)
            lines.append("### Execution Contract Evidence")
            lines.append(f"- Samples: {summary['samples']}")
            if summary["average_score"] is not None:
                lines.append(f"- Average score: {summary['average_score']:.2f}")
            if summary["lowest_criteria"]:
                lines.append(f"- Lowest criteria: {', '.join(summary['lowest_criteria'])}")
            lines.append("")

        return "\n".join(lines)

    def _evidence_payload(self, packet: IterationDecisionPacket) -> dict[str, Any]:
        feedback = packet.feedback or summarise_skill_feedback(packet.executions)
        return {
            "origin": packet.skill_origin,
            "execution_summary": self._execution_contract_evidence_summary(packet),
            "outcome_summary": packet.outcome_summary or {},
            "feedback_summary": feedback.evidence,
            "lifecycle_context": {
                "lifecycle_events": len(packet.lifecycle_history),
                "recent_actions": [event["action"] for event in packet.lifecycle_history[:5]],
            },
            "exemplar_count": len(packet.exemplar_slice),
        }

    def _lifecycle_evidence(self, packet: IterationDecisionPacket) -> dict[str, Any]:
        return self._evidence_payload(packet)
