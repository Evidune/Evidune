"""Outcome-driven iteration workflow built on shared harness records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agent.harness.models import BudgetSummary, DecisionRecord, HarnessTask, TaskBrief
from agent.harness.profiles import get_squad_profile
from core.updater import UpdateResult, replace_section, update_reference


@dataclass
class IterationDecision:
    decision: str
    update: UpdateResult
    task: HarnessTask


class IterationHarness:
    """Deterministic workflow for keep/rewrite/rollback/disable decisions."""

    def __init__(self, memory) -> None:
        self.memory = memory

    def run(
        self,
        *,
        skill,
        result,
        feedback,
        current: str,
    ) -> IterationDecision:
        task = HarnessTask(
            id=f"iter-{skill.name}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
            conversation_id="",
            surface="run",
            squad=get_squad_profile("iteration"),
            brief=TaskBrief(user_input=f"Iterate skill {skill.name}", selected_skills=[skill.name]),
            budget_summary=BudgetSummary(max_rounds=1),
        )
        self.memory.create_harness_task(
            task_id=task.id,
            conversation_id="",
            surface="run",
            squad_profile="iteration",
            status="running",
            task_kind="skill_iteration",
            user_input=skill.name,
            selected_skills=[skill.name],
            role_roster=task.squad.roles,
            budget=task.budget_summary.to_dict(),
        )

        evidence = {
            "top_titles": [record.title for record in result.top_performers],
            "patterns": list(result.patterns),
            "feedback": feedback.evidence,
        }
        evidence_step = self.memory.record_harness_step(
            task.id,
            phase="collect",
            role="evidence_collector",
            status="completed",
            summary=self._summarise(f"Collected evidence for {skill.name}"),
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

        reference_content = self.build_skill_reference_content(skill.update_section, result)
        decision = "keep"
        proposed_content = ""

        if feedback.should_disable:
            event = self.memory.get_latest_skill_lifecycle_event(skill.name, action="rewrite")
            if event and event.get("content_before"):
                decision = "rollback"
                proposed_content = self._build_rollback_content(
                    restored=event["content_before"],
                    section=skill.update_section,
                    reference_content=reference_content,
                )
            elif (self.memory.get_emerged_skill(skill.name) or {}).get("status") == "active":
                decision = "disable"
        elif self._has_metric_evidence(result) and feedback.should_rewrite:
            proposed_content = self._build_rewrite_content(
                current=current,
                result=result,
                feedback=feedback,
                section=skill.update_section,
                reference_content=reference_content,
            )
            if proposed_content:
                decision = "rewrite"

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
            accepted=decision in {"keep", "disable"},
            meta={"decision": decision},
        )

        reviewer_ok = decision != "rewrite" or bool(
            proposed_content and proposed_content != current
        )
        reviewer_step = self.memory.record_harness_step(
            task.id,
            phase="review",
            role="safety_reviewer",
            status="completed",
            summary=self._summarise("Approved rewrite" if reviewer_ok else "Rejected rewrite"),
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
        if decision == "rewrite" and not reviewer_ok:
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

        update = self._apply_decision(
            decision=decision,
            skill=skill,
            current=current,
            proposed_content=proposed_content,
            feedback=feedback,
            reference_content=reference_content,
        )
        task.decision = DecisionRecord(decision=decision, rationale=f"Decision={decision}")
        task.status = "completed"
        task.final_output = decision
        task.convergence_summary = {"decision": decision}
        self.memory.record_harness_artifact(
            task.id,
            step_id=arbiter_step,
            phase="decide",
            role="lifecycle_arbiter",
            kind="decision",
            summary=self._summarise(decision),
            content=decision,
            accepted=True,
            meta={"path": update.path, "has_changes": update.has_changes},
        )
        self.memory.update_harness_task(
            task.id,
            status="completed",
            summary=self._summarise(decision),
            convergence=task.convergence_summary,
            final_output=decision,
            budget=task.budget_summary.to_dict(),
        )
        return IterationDecision(decision=decision, update=update, task=task)

    def _apply_decision(
        self,
        *,
        decision: str,
        skill,
        current: str,
        proposed_content: str,
        feedback,
        reference_content: str,
    ) -> UpdateResult:
        if decision == "rewrite" and proposed_content:
            Path(skill.path).write_text(proposed_content, encoding="utf-8")
            self.memory.record_skill_lifecycle_event(
                skill.name,
                "rewrite",
                path=str(skill.path),
                reason="Outcome-driven rewrite approved by iteration harness",
                evidence=feedback.evidence,
                content_before=current,
                content_after=proposed_content,
            )
            return UpdateResult(
                path=str(skill.path),
                strategy="skill_rewrite",
                has_changes=True,
                old_content=current,
                new_content=proposed_content,
            )

        if decision == "rollback" and proposed_content:
            Path(skill.path).write_text(proposed_content, encoding="utf-8")
            self.memory.record_skill_lifecycle_event(
                skill.name,
                "rollback",
                status="rolled_back",
                path=str(skill.path),
                reason="Iteration harness rolled back the last automatic rewrite",
                evidence=feedback.evidence,
                content_before=current,
                content_after=proposed_content,
            )
            return UpdateResult(
                path=str(skill.path),
                strategy="skill_rollback",
                has_changes=True,
                old_content=current,
                new_content=proposed_content,
            )

        if decision == "disable":
            self.memory.set_emerged_skill_status(
                skill.name,
                "disabled",
                reason="Iteration harness disabled the emerged skill after negative evidence",
                evidence=feedback.evidence,
            )
            self.memory.record_skill_lifecycle_event(
                skill.name,
                "disable",
                status="disabled",
                path=str(skill.path),
                reason="Iteration harness disabled the emerged skill",
                evidence=feedback.evidence,
            )
            return update_reference(
                path=skill.path,
                strategy="replace_section",
                new_content=reference_content,
                section=skill.update_section,
            )

        return update_reference(
            path=skill.path,
            strategy="replace_section",
            new_content=reference_content,
            section=skill.update_section,
        )

    @staticmethod
    def _has_metric_evidence(result) -> bool:
        return bool(result.top_performers) and (
            len(result.top_performers) >= 2 or bool(result.patterns)
        )

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
        result,
        feedback,
        section: str,
        reference_content: str,
    ) -> str:
        from core.iteration_helpers import _build_rewritten_instructions

        prefix, body = self._split_frontmatter(current)
        instructions_body = self._extract_section(body, "## Instructions")
        if instructions_body is None:
            return ""
        rewritten = _build_rewritten_instructions(instructions_body, result, feedback)
        new_body = replace_section(body, "## Instructions", rewritten)
        new_body = replace_section(new_body, section, reference_content)
        return (prefix + new_body.rstrip() + "\n") if prefix else (new_body.rstrip() + "\n")

    def _build_rollback_content(
        self,
        *,
        restored: str,
        section: str,
        reference_content: str,
    ) -> str:
        prefix, body = self._split_frontmatter(restored)
        updated_body = replace_section(body, section, reference_content)
        return (prefix + updated_body.rstrip() + "\n") if prefix else (updated_body.rstrip() + "\n")

    @staticmethod
    def _extract_section(body: str, heading: str) -> str | None:
        import re

        heading_re = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
        target = heading.lstrip("#").strip()
        lines = body.split("\n")
        start_idx: int | None = None
        start_level: int | None = None
        for i, line in enumerate(lines):
            match = heading_re.match(line)
            if not match:
                continue
            level = len(match.group(1))
            title = match.group(2).strip()
            if start_idx is None and title == target:
                start_idx = i
                start_level = level
            elif start_idx is not None and level <= (start_level or 0):
                return "\n".join(lines[start_idx + 1 : i]).strip()
        if start_idx is not None:
            return "\n".join(lines[start_idx + 1 :]).strip()
        return None

    @staticmethod
    def build_skill_reference_content(section: str, result) -> str:
        lines = [section, ""]
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"*Auto-updated by aiflay on {timestamp}*")
        lines.append("")
        if result.top_performers:
            lines.append("### Top Performers")
            for idx, record in enumerate(result.top_performers, start=1):
                metrics = ", ".join(f"{key}={value}" for key, value in record.metrics.items())
                lines.append(f"{idx}. **{record.title}** — {metrics}")
            lines.append("")
        if result.patterns:
            lines.append("### Patterns")
            for pattern in result.patterns:
                lines.append(f"- {pattern}")
            lines.append("")
        return "\n".join(lines)
