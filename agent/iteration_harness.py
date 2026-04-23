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
    top_performers: list[dict[str, Any]] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    executions: list[dict[str, Any]] = field(default_factory=list)
    evaluation_contract: dict[str, Any] | None = None
    contract_evaluations: list[dict[str, Any]] = field(default_factory=list)
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
    top_performers: list[dict[str, Any]],
    patterns: list[str],
    feedback: SkillFeedbackSummary,
) -> str:
    base = _strip_managed_adjustments(instructions_body).rstrip()
    lines = ["## Instructions", ""]
    if base:
        lines.append(base)
        lines.append("")

    lines.append(_MANAGED_ADJUSTMENTS_HEADING)
    lines.append("")
    if top_performers:
        exemplar_titles = ", ".join(item["title"] for item in top_performers[:2])
        lines.append(
            f"- Mirror the specificity and framing seen in top performers such as: {exemplar_titles}."
        )
    for pattern in patterns:
        lines.append(f"- Reinforce this observed pattern: {pattern}.")
    lines.append(
        "- Keep the manual guidance above intact; treat these adjustments as evidence-backed overrides only."
    )
    if feedback.average_score is not None:
        lines.append(
            f"- Recent evaluator average is {feedback.average_score:.2f}; keep iterating only while evidence stays positive."
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
    contract_row = memory.get_skill_evaluation_contract(skill.name)
    contract = contract_row.get("contract") if contract_row else None
    contract_evaluations = memory.list_skill_evaluations(skill.name, limit=20)
    top_performers = []
    patterns: list[str] = []
    metrics_summary: dict[str, Any] = {}
    if result is not None:
        top_performers = [
            {
                "title": record.title,
                "metrics": dict(record.metrics),
                "metadata": dict(getattr(record, "metadata", {}) or {}),
            }
            for record in result.top_performers
        ]
        patterns = list(result.patterns)
        metrics_summary = {
            "top_performer_count": len(top_performers),
            "pattern_count": len(patterns),
            "top_titles": [record["title"] for record in top_performers],
        }

    return IterationDecisionPacket(
        skill_name=skill.name,
        skill_path=str(skill.path),
        skill_origin=origin,
        update_section=skill.update_section,
        current_content=current,
        metrics_summary=metrics_summary,
        top_performers=top_performers,
        patterns=patterns,
        executions=executions,
        evaluation_contract=contract,
        contract_evaluations=contract_evaluations,
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
        if decision in {"rewrite", "rollback"} and not reviewer_ok:
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
        contract_decision = self._contract_decision(packet)

        if contract_decision == "disable":
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

        if feedback.should_disable and not (
            packet.contract_evaluations and feedback.signal_samples == 0
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

        if contract_decision == "rewrite":
            proposed = self._build_rewrite_content(
                current=packet.current_content,
                packet=packet,
                reference_content=reference_content,
            )
            if proposed:
                return "rewrite", proposed

        if self._has_metric_evidence(packet) and feedback.should_rewrite:
            proposed = self._build_rewrite_content(
                current=packet.current_content,
                packet=packet,
                reference_content=reference_content,
            )
            if proposed:
                return "rewrite", proposed

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
    def _has_metric_evidence(packet: IterationDecisionPacket) -> bool:
        return bool(packet.top_performers) and (
            len(packet.top_performers) >= 2 or bool(packet.patterns)
        )

    @staticmethod
    def _contract_evidence_summary(packet: IterationDecisionPacket) -> dict[str, Any]:
        scores = [
            float(item["aggregate_score"])
            for item in packet.contract_evaluations
            if item.get("aggregate_score") is not None
        ]
        criteria_totals: dict[str, list[float]] = {}
        for item in packet.contract_evaluations:
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

    def _contract_decision(self, packet: IterationDecisionPacket) -> str:
        contract = packet.evaluation_contract or {}
        summary = self._contract_evidence_summary(packet)
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
        import re

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
            packet.top_performers,
            packet.patterns,
            packet.feedback or summarise_skill_feedback(packet.executions),
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

    def _build_reference_content(self, packet: IterationDecisionPacket) -> str:
        if not packet.top_performers and not packet.patterns and not packet.contract_evaluations:
            return ""
        lines = [packet.update_section, ""]
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"*Auto-updated by evidune on {timestamp}*")
        lines.append("")

        if packet.top_performers:
            lines.append("### Top Performers")
            for idx, performer in enumerate(packet.top_performers, 1):
                metrics_str = ", ".join(
                    f"{key}={value}" for key, value in performer.get("metrics", {}).items()
                )
                lines.append(f"{idx}. **{performer['title']}** — {metrics_str}")
            lines.append("")

        if packet.patterns:
            lines.append("### Patterns")
            for pattern in packet.patterns:
                lines.append(f"- {pattern}")
            lines.append("")

        if packet.contract_evaluations:
            summary = self._contract_evidence_summary(packet)
            lines.append("### Evaluation Contract Evidence")
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
            "metrics_summary": packet.metrics_summary,
            "top_titles": [item["title"] for item in packet.top_performers],
            "patterns": list(packet.patterns),
            "execution_count": len(packet.executions),
            "evaluation_contract": packet.evaluation_contract or {},
            "contract_evaluation_summary": self._contract_evidence_summary(packet),
            "feedback": feedback.evidence,
            "lifecycle_events": len(packet.lifecycle_history),
        }

    def _lifecycle_evidence(self, packet: IterationDecisionPacket) -> dict[str, Any]:
        payload = self._evidence_payload(packet)
        payload["recent_lifecycle_actions"] = [
            event["action"] for event in packet.lifecycle_history[:5]
        ]
        return payload
