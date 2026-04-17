"""Swarm harness orchestration for bounded multi-role tasks."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent.harness.models import (
    BudgetSummary,
    DecisionRecord,
    HarnessTask,
    TaskBrief,
    TaskEvent,
    WorkArtifact,
)
from agent.harness.runtime import RuntimeEnvironment
from agent.tools.registry import ToolRegistry
from skills.loader import Skill

EventSink = Callable[[TaskEvent], None]


@dataclass
class RoleExecutionResult:
    text: str
    tool_trace: list[dict[str, Any]]
    estimated_tokens: int


class SwarmHarness:
    """Deterministic control plane that schedules planner/worker/critic/synthesizer turns."""

    def __init__(
        self,
        *,
        llm,
        memory,
        system_prompt: str = "",
        max_tool_iterations: int = 8,
        max_rounds: int = 2,
        max_worker_branches: int = 2,
        token_budget: int = 20_000,
        tool_call_budget: int = 16,
        wall_clock_budget_s: int = 120,
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.system_prompt = system_prompt
        self.max_tool_iterations = max_tool_iterations
        self.max_rounds = max_rounds
        self.max_worker_branches = max_worker_branches
        self.token_budget = token_budget
        self.tool_call_budget = tool_call_budget
        self.wall_clock_budget_s = wall_clock_budget_s

    async def run(
        self,
        *,
        brief: TaskBrief,
        squad,
        task_id: str | None = None,
        environment: RuntimeEnvironment | None = None,
        identity_prompt: str = "",
        worker_skill_groups: list[list[Skill]] | None = None,
        tool_registry_by_role: dict[str, ToolRegistry | None] | None = None,
        event_sink: EventSink | None = None,
        surface: str = "serve",
    ) -> HarnessTask:
        task_id = task_id or f"task-{uuid.uuid4().hex[:10]}"
        start = time.monotonic()
        task = HarnessTask(
            id=task_id,
            conversation_id=brief.conversation_id,
            surface=surface,
            squad=squad,
            brief=brief,
            environment_id=environment.environment_id if environment else "",
            environment_status="provisioned" if environment else "",
            budget_summary=BudgetSummary(
                token_budget=self.token_budget,
                tool_call_budget=self.tool_call_budget,
                wall_clock_budget_s=self.wall_clock_budget_s,
                max_rounds=max(1, min(self.max_rounds, 2)),
            ),
        )
        self.memory.create_harness_task(
            task_id=task.id,
            conversation_id=task.conversation_id,
            surface=surface,
            squad_profile=squad.name,
            status=task.status,
            task_kind="conversation",
            user_input=brief.user_input,
            selected_skills=brief.selected_skills,
            role_roster=squad.role_roster(),
            budget=task.budget_summary.to_dict(),
            environment_id=task.environment_id,
            environment_status=task.environment_status,
        )

        emit = self._build_emitter(task, event_sink)
        emit("task_started", phase="plan", message="Swarm task started", data={"squad": squad.name})
        if environment is not None:
            emit(
                "environment_started",
                phase="environment",
                role="system",
                message="Runtime environment provisioned",
                data={
                    "environment_id": environment.environment_id,
                    "root": str(environment.root),
                    "artifacts_dir": str(environment.artifacts_dir),
                },
            )

        tool_registry_by_role = tool_registry_by_role or {}
        skill_groups = worker_skill_groups or [[] for _ in range(squad.worker_branches)]
        accepted_worker_ids: list[int] = []
        latest_worker_ids: list[int] = []
        prior_artifacts: list[WorkArtifact] = []
        last_decision = DecisionRecord(decision="keep")

        for round_idx in range(task.budget_summary.max_rounds):
            task.budget_summary.rounds_used = round_idx + 1
            plan_result = await self._run_role(
                role="planner",
                phase="plan",
                brief=brief,
                identity_prompt=identity_prompt,
                prior_artifacts=prior_artifacts,
                attached_skills=[],
                tool_registry=tool_registry_by_role.get("planner"),
            )
            self._consume_budget(task, plan_result, start)
            plan_step_id = self.memory.record_harness_step(
                task.id,
                phase="plan",
                role="planner",
                status="completed",
                summary=self._summarise(plan_result.text),
                tool_trace=plan_result.tool_trace,
                budget=task.budget_summary.to_dict(),
            )
            plan_artifact = self._record_artifact(
                task,
                step_id=plan_step_id,
                phase="plan",
                role="planner",
                kind="plan",
                summary=self._summarise(plan_result.text),
                content=plan_result.text,
                accepted=True,
                meta={"round": round_idx + 1},
            )
            prior_artifacts = [plan_artifact]
            emit(
                "phase_completed",
                phase="plan",
                role="planner",
                message="Planner produced a compact task brief",
                data={"summary": plan_artifact.summary, "round": round_idx + 1},
            )
            if self._budget_exhausted(task, start):
                break

            latest_worker_ids = []
            for worker_idx in range(min(squad.worker_branches, self.max_worker_branches)):
                role = f"worker-{worker_idx + 1}"
                execute_result = await self._run_role(
                    role=role,
                    phase="execute",
                    brief=brief,
                    identity_prompt=identity_prompt,
                    prior_artifacts=prior_artifacts,
                    attached_skills=(
                        skill_groups[worker_idx] if worker_idx < len(skill_groups) else []
                    ),
                    tool_registry=tool_registry_by_role.get("worker"),
                )
                self._consume_budget(task, execute_result, start)
                step_id = self.memory.record_harness_step(
                    task.id,
                    phase="execute",
                    role=role,
                    status="completed",
                    summary=self._summarise(execute_result.text),
                    tool_trace=execute_result.tool_trace,
                    budget=task.budget_summary.to_dict(),
                )
                artifact = self._record_artifact(
                    task,
                    step_id=step_id,
                    phase="execute",
                    role=role,
                    kind="work",
                    summary=self._summarise(execute_result.text),
                    content=execute_result.text,
                    accepted=False,
                    meta={"skills": [skill.name for skill in skill_groups[worker_idx]]},
                )
                latest_worker_ids.append(artifact.id)
                emit(
                    "phase_completed",
                    phase="execute",
                    role=role,
                    message="Worker branch completed",
                    data={
                        "summary": artifact.summary,
                        "skills": [skill.name for skill in skill_groups[worker_idx]],
                        "tool_calls": len(execute_result.tool_trace),
                    },
                )
                if self._budget_exhausted(task, start):
                    break

            if self._budget_exhausted(task, start):
                break

            worker_artifacts = [
                artifact for artifact in task.artifacts if artifact.id in latest_worker_ids
            ]
            persisted_task = self.memory.get_harness_task(task.id) or {}
            validation_summary = dict(persisted_task.get("validation_summary") or {})
            validation_status = validation_summary.get("status", "pending")
            emit(
                "validation_started",
                phase="validate",
                role="validator",
                message="Recording validation evidence before critique",
                data=validation_summary or {"status": "pending"},
            )
            validation_step_id = self.memory.record_harness_step(
                task.id,
                phase="validate",
                role="validator",
                status=validation_status,
                summary=self._summarise(
                    validation_summary.get("message")
                    or validation_summary.get("status")
                    or "Validation pending"
                ),
                tool_trace=[],
                budget=task.budget_summary.to_dict(),
            )
            validation_artifact_id = self.memory.record_harness_artifact(
                task.id,
                step_id=validation_step_id,
                phase="validate",
                role="validator",
                kind="validation",
                summary=self._summarise(json.dumps(validation_summary or {"status": "pending"})),
                content=json.dumps(validation_summary or {"status": "pending"}),
                accepted=validation_status != "failed",
                meta=validation_summary or {"status": "pending"},
            )
            validation_artifact = WorkArtifact(
                id=validation_artifact_id,
                task_id=task.id,
                step_id=validation_step_id,
                phase="validate",
                role="validator",
                kind="validation",
                summary=self._summarise(json.dumps(validation_summary or {"status": "pending"})),
                content=json.dumps(validation_summary or {"status": "pending"}),
                accepted=validation_status != "failed",
                meta=validation_summary or {"status": "pending"},
            )
            task.artifacts.append(validation_artifact)
            emit(
                "validation_failed" if validation_status == "failed" else "validation_passed",
                phase="validate",
                role="validator",
                message="Validation summary recorded before critique",
                data=validation_summary or {"status": "pending"},
            )
            critique_result = await self._run_role(
                role="critic",
                phase="critique",
                brief=brief,
                identity_prompt=identity_prompt,
                prior_artifacts=worker_artifacts + [validation_artifact],
                attached_skills=[],
                tool_registry=tool_registry_by_role.get("critic"),
            )
            self._consume_budget(task, critique_result, start)
            critique_step_id = self.memory.record_harness_step(
                task.id,
                phase="critique",
                role="critic",
                status="completed",
                summary=self._summarise(critique_result.text),
                tool_trace=critique_result.tool_trace,
                budget=task.budget_summary.to_dict(),
            )
            critique_artifact = self._record_artifact(
                task,
                step_id=critique_step_id,
                phase="critique",
                role="critic",
                kind="review",
                summary=self._summarise(critique_result.text),
                content=critique_result.text,
                accepted=True,
                meta={"round": round_idx + 1},
            )
            if self._critic_accepts(critique_result.text):
                accepted_worker_ids = list(latest_worker_ids)
                for artifact_id in accepted_worker_ids:
                    self.memory.set_harness_artifact_accepted(artifact_id, accepted=True)
                    for artifact in task.artifacts:
                        if artifact.id == artifact_id:
                            artifact.accepted = True
                last_decision = DecisionRecord(
                    decision="accept",
                    rationale=critique_artifact.summary,
                    accepted_artifact_ids=list(accepted_worker_ids),
                )
                emit(
                    "convergence",
                    phase="converge",
                    role="critic",
                    message="Critic accepted the worker outputs",
                    data={"accepted_artifacts": accepted_worker_ids},
                )
                break

            last_decision = DecisionRecord(
                decision="rework",
                rationale=critique_artifact.summary,
                rejected_artifact_ids=list(latest_worker_ids),
                stop_reason="critic_rejected",
            )
            emit(
                "convergence",
                phase="converge",
                role="critic",
                message="Critic requested one rework cycle",
                data={"rejected_artifacts": latest_worker_ids, "round": round_idx + 1},
            )
            if round_idx + 1 >= task.budget_summary.max_rounds:
                task.status = "partial"
            prior_artifacts = [plan_artifact, critique_artifact]

        if not accepted_worker_ids:
            accepted_worker_ids = list(latest_worker_ids[:1])
        task.convergence_summary = {
            "decision": last_decision.decision,
            "accepted_artifacts": list(accepted_worker_ids),
            "rejected_artifacts": list(last_decision.rejected_artifact_ids),
            "rationale": last_decision.rationale,
        }

        synth_artifacts = [
            artifact for artifact in task.artifacts if artifact.id in accepted_worker_ids
        ]
        synth_result = await self._run_role(
            role="synthesizer",
            phase="finalise",
            brief=brief,
            identity_prompt=identity_prompt,
            prior_artifacts=synth_artifacts,
            attached_skills=[],
            tool_registry=tool_registry_by_role.get("synthesizer"),
        )
        self._consume_budget(task, synth_result, start)
        final_step_id = self.memory.record_harness_step(
            task.id,
            phase="finalise",
            role="synthesizer",
            status="completed",
            summary=self._summarise(synth_result.text),
            tool_trace=synth_result.tool_trace,
            budget=task.budget_summary.to_dict(),
        )
        self._record_artifact(
            task,
            step_id=final_step_id,
            phase="finalise",
            role="synthesizer",
            kind="final",
            summary=self._summarise(synth_result.text),
            content=synth_result.text,
            accepted=True,
        )
        task.final_output = synth_result.text
        task.decision = last_decision
        if task.status == "running":
            task.status = "completed" if last_decision.decision == "accept" else "partial"
        task.budget_summary.elapsed_ms = int((time.monotonic() - start) * 1000)
        persisted_task = self.memory.get_harness_task(task.id) or {}
        task.environment_status = persisted_task.get("environment_status", task.environment_status)
        task.artifact_manifest = dict(persisted_task.get("artifact_manifest") or {})
        task.validation_summary = dict(persisted_task.get("validation_summary") or {})
        task.delivery_summary = dict(persisted_task.get("delivery_summary") or {})
        task.escalation_reason = persisted_task.get("escalation_reason", "")
        self.memory.update_harness_task(
            task.id,
            status=task.status,
            summary=self._summarise(task.final_output),
            convergence=task.convergence_summary,
            final_output=task.final_output,
            budget=task.budget_summary.to_dict(),
            artifact_manifest=task.artifact_manifest,
            validation_summary=task.validation_summary,
            delivery_summary=task.delivery_summary,
            escalation_reason=task.escalation_reason,
            environment_status=task.environment_status,
        )
        emit(
            "task_completed",
            phase="finalise",
            role="synthesizer",
            message="Swarm task finished",
            data={
                "status": task.status,
                "decision": last_decision.decision,
                "budget": task.budget_summary.to_dict(),
            },
        )
        return task

    async def _run_role(
        self,
        *,
        role: str,
        phase: str,
        brief: TaskBrief,
        identity_prompt: str,
        prior_artifacts: list[WorkArtifact],
        attached_skills: list[Skill],
        tool_registry: ToolRegistry | None,
    ) -> RoleExecutionResult:
        messages = self._build_messages(
            role=role,
            phase=phase,
            brief=brief,
            identity_prompt=identity_prompt,
            prior_artifacts=prior_artifacts,
            attached_skills=attached_skills,
        )
        if not tool_registry or len(tool_registry) == 0:
            text = await self.llm.complete(messages)
            return RoleExecutionResult(
                text=text,
                tool_trace=[],
                estimated_tokens=self._estimate_tokens(messages, text),
            )

        working = list(messages)
        tool_trace: list[dict[str, Any]] = []
        for _ in range(self.max_tool_iterations):
            result = await self.llm.complete_with_tools(working, tool_registry.all())
            if not result.tool_calls:
                text = result.text or ""
                return RoleExecutionResult(
                    text=text,
                    tool_trace=tool_trace,
                    estimated_tokens=self._estimate_tokens(working, text),
                )

            working.append(
                {
                    "role": "assistant",
                    "content": result.text or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments),
                            },
                        }
                        for call in result.tool_calls
                    ],
                }
            )
            for call in result.tool_calls:
                tool_result = await tool_registry.execute(call)
                tool_trace.append(
                    {
                        "role": role,
                        "name": call.name,
                        "arguments": call.arguments,
                        "result": tool_result.content,
                        "is_error": tool_result.is_error,
                    }
                )
                working.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": tool_result.content,
                    }
                )

        final_text = await self.llm.complete(working)
        return RoleExecutionResult(
            text=final_text,
            tool_trace=tool_trace,
            estimated_tokens=self._estimate_tokens(working, final_text),
        )

    def _build_messages(
        self,
        *,
        role: str,
        phase: str,
        brief: TaskBrief,
        identity_prompt: str,
        prior_artifacts: list[WorkArtifact],
        attached_skills: list[Skill],
    ) -> list[dict[str, str]]:
        system_parts = []
        if identity_prompt:
            system_parts.append(identity_prompt)
        if self.system_prompt:
            system_parts.append(self.system_prompt)
        system_parts.append(self._role_instructions(role, phase))

        artifact_lines: list[str] = []
        if prior_artifacts:
            artifact_lines.extend(["# Accepted Context", ""])
            for artifact in prior_artifacts:
                artifact_lines.append(
                    f"- [{artifact.phase}/{artifact.role}] {artifact.summary or self._summarise(artifact.content)}"
                )
            artifact_lines.append("")
        if attached_skills:
            artifact_lines.extend(["# Attached Specialist Skills", ""])
            for skill in attached_skills[:2]:
                artifact_lines.append(f"## {skill.name}")
                if skill.description:
                    artifact_lines.append(skill.description)
                if skill.instructions:
                    artifact_lines.append(skill.instructions)
                artifact_lines.append("")

        user_lines = [
            "# Task Brief",
            f"User request: {brief.user_input}",
            f"Conversation id: {brief.conversation_id or '(none)'}",
            f"Mode: {brief.mode}",
        ]
        if brief.selected_skills and not role.startswith("worker"):
            user_lines.append("Matched skills: " + ", ".join(brief.selected_skills))
        if brief.facts:
            user_lines.extend(["", "# Relevant Facts"])
            for fact in brief.facts:
                user_lines.append(f"- {fact['key']}: {fact['value']}")
        if brief.history:
            user_lines.extend(["", "# Recent History"])
            for message in brief.history[-4:]:
                user_lines.append(f"- {message['role']}: {message['content'][:200]}")
        if artifact_lines:
            user_lines.extend(["", *artifact_lines])

        return [
            {"role": "system", "content": "\n\n".join(system_parts)},
            {"role": "user", "content": "\n".join(user_lines)},
        ]

    def _build_emitter(
        self,
        task: HarnessTask,
        event_sink: EventSink | None,
    ) -> Callable[[str], None]:
        def emit(
            event_type: str,
            *,
            phase: str = "",
            role: str = "",
            message: str = "",
            data: dict[str, Any] | None = None,
        ) -> None:
            event = TaskEvent(
                sequence=len(task.events) + 1,
                type=event_type,
                phase=phase,
                role=role,
                message=message,
                data=data or {},
            )
            task.events.append(event)
            if event_sink is not None:
                event_sink(event)

        return emit

    def _record_artifact(
        self,
        task: HarnessTask,
        *,
        step_id: int,
        phase: str,
        role: str,
        kind: str,
        summary: str,
        content: str,
        accepted: bool,
        meta: dict[str, Any] | None = None,
    ) -> WorkArtifact:
        artifact_id = self.memory.record_harness_artifact(
            task.id,
            step_id=step_id,
            phase=phase,
            role=role,
            kind=kind,
            summary=summary,
            content=content,
            accepted=accepted,
            meta=meta or {},
        )
        artifact = WorkArtifact(
            id=artifact_id,
            task_id=task.id,
            step_id=step_id,
            phase=phase,
            role=role,
            kind=kind,
            summary=summary,
            content=content,
            accepted=accepted,
            meta=meta or {},
        )
        task.artifacts.append(artifact)
        return artifact

    def _role_instructions(self, role: str, phase: str) -> str:
        if role == "planner":
            return "\n".join(
                [
                    f"# Role: planner ({phase})",
                    "Produce a concise execution plan with no filler.",
                    "Keep the plan bounded and explicit.",
                ]
            )
        if role.startswith("worker"):
            return "\n".join(
                [
                    f"# Role: {role} ({phase})",
                    "Execute one bounded branch of the task.",
                    "Use attached skills only when they materially help this branch.",
                    "Return a compact result with the key actions and outputs.",
                ]
            )
        if role == "critic":
            return "\n".join(
                [
                    f"# Role: critic ({phase})",
                    "Review the worker outputs against the task brief.",
                    "Start with ACCEPT or REJECT on the first line.",
                    "If rejecting, explain the smallest necessary rework.",
                ]
            )
        return "\n".join(
            [
                f"# Role: {role} ({phase})",
                "Synthesize the accepted worker outputs into one clear final answer.",
                "Prefer a concise answer over a process recap.",
            ]
        )

    def _consume_budget(self, task: HarnessTask, result: RoleExecutionResult, start: float) -> None:
        task.budget_summary.token_used += result.estimated_tokens
        task.budget_summary.tool_calls_used += len(result.tool_trace)
        task.budget_summary.elapsed_ms = int((time.monotonic() - start) * 1000)

    def _budget_exhausted(self, task: HarnessTask, start: float) -> bool:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        task.budget_summary.elapsed_ms = elapsed_ms
        if task.budget_summary.token_budget and (
            task.budget_summary.token_used >= task.budget_summary.token_budget
        ):
            task.status = "partial"
            task.budget_summary.stopped_reason = "token_budget_exhausted"
            return True
        if task.budget_summary.tool_call_budget and (
            task.budget_summary.tool_calls_used >= task.budget_summary.tool_call_budget
        ):
            task.status = "partial"
            task.budget_summary.stopped_reason = "tool_call_budget_exhausted"
            return True
        if task.budget_summary.wall_clock_budget_s and (
            elapsed_ms >= task.budget_summary.wall_clock_budget_s * 1000
        ):
            task.status = "partial"
            task.budget_summary.stopped_reason = "wall_clock_budget_exhausted"
            return True
        return False

    @staticmethod
    def _critic_accepts(text: str) -> bool:
        first_line = (text or "").strip().splitlines()[0].upper() if text.strip() else ""
        if first_line.startswith("REJECT"):
            return False
        if first_line.startswith("ACCEPT"):
            return True
        lowered = text.lower()
        return "reject" not in lowered and "rework" not in lowered and "missing" not in lowered

    @staticmethod
    def _summarise(text: str, limit: int = 180) -> str:
        raw = " ".join((text or "").split())
        if len(raw) <= limit:
            return raw
        return raw[: limit - 1] + "…"

    @staticmethod
    def _estimate_tokens(messages: list[dict[str, Any]], text: str) -> int:
        payload = json.dumps(messages, ensure_ascii=False, default=str)
        return max(1, (len(payload) + len(text)) // 4)
