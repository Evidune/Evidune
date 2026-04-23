"""Agent core — orchestrates skills, memory, identity, and LLM."""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.fact_extractor import FactExtractor
from agent.harness import TaskBrief
from agent.harness.profiles import get_squad_profile
from agent.harness.swarm import SwarmHarness
from agent.iteration_harness import IterationHarness, build_decision_packet
from agent.llm import LLMClient
from agent.pattern_detector import PatternDetector
from agent.self_evaluator import SelfEvaluator
from agent.skill_synthesizer import SkillSynthesizer
from agent.title_generator import TitleGenerator
from agent.tools.base import ToolCall
from agent.tools.internal import conversation_tools, memory_tools, plan_tools, skill_tools
from agent.tools.registry import ToolRegistry
from gateway.base import InboundMessage, OutboundMessage
from identities.loader import Identity
from identities.registry import IdentityRegistry
from memory.store import MemoryStore
from skills.evaluation import (
    contract_summary,
    parse_evaluation_contract,
    upsert_contract_frontmatter,
)
from skills.loader import Skill, parse_skill
from skills.models import SkillMatch
from skills.registry import SkillRegistry

_CONVERSATION_MODES = {"plan", "execute"}
_SKILL_TARGET_RE = re.compile(
    r"\b(skills?|capabilit(?:y|ies)|workflows?)\b|skill|能力|工作流|可复用",
    re.IGNORECASE,
)
_SKILL_ACTION_RE = re.compile(
    r"\b(create|build|generate|implement|synthesi[sz]e|make|turn\s+.*\s+into)\b"
    r"|建立|创建|新建|生成|实现|沉淀|形成|做成|变成|封装|提炼|设计",
    re.IGNORECASE,
)


def _is_explicit_skill_request(text: str) -> bool:
    """Return True when the user is directly asking to create a reusable skill."""
    if not text:
        return False
    return bool(_SKILL_TARGET_RE.search(text) and _SKILL_ACTION_RE.search(text))


@dataclass
class EmergenceDecision:
    conversation_id: str
    mode: str
    matched_skills: list[str] = field(default_factory=list)
    execution_skill_names: list[str] = field(default_factory=list)
    emergence_counter: int = 0
    emergence_attempted: bool = False
    skip_reason: str = ""
    detected_name: str = ""
    detected_confidence: float | None = None
    activation_status: str = "skipped"
    emerged_skill_path: str = ""
    emerged_skill: str | None = None
    trigger_reason: str = ""
    skill_snapshot_count: int = 0
    skill_prompt_token_estimate: int = 0
    skill_match_reasons: dict[str, list[str]] = field(default_factory=dict)
    skill_creation: dict[str, Any] | None = None
    resolver_action: str = ""
    duplicate_of: str = ""
    load_error: str = ""
    evaluation_contract_status: str = ""
    aggregate_score: float | None = None
    lowest_criteria: str = ""
    observed_metric_count: int = 0
    evaluation_samples: int = 0


class AgentCore:
    """Central agent that handles messages using skills + memory + identity + LLM."""

    def __init__(
        self,
        llm: LLMClient,
        skill_registry: SkillRegistry,
        memory: MemoryStore,
        system_prompt: str = "",
        skill_prompt_mode: str = "auto",
        max_history: int = 20,
        identity_registry: IdentityRegistry | None = None,
        fact_extractor: FactExtractor | None = None,
        fact_extraction_every_n_turns: int = 5,
        fact_extraction_min_confidence: float = 0.7,
        self_evaluator: SelfEvaluator | None = None,
        pattern_detector: PatternDetector | None = None,
        skill_synthesizer: SkillSynthesizer | None = None,
        emergence_every_n_turns: int = 10,
        emergence_min_confidence: float = 0.7,
        emergence_inline_timeout_s: float = 5.0,
        title_generator: TitleGenerator | None = None,
        title_after_turns: int = 3,
        tool_registry: ToolRegistry | None = None,
        max_tool_iterations: int = 8,
        skill_contract_auto_update: bool = True,
        harness_config: object | None = None,
        base_dir: Path | None = None,
        config_path: Path | None = None,
        runtime_manager=None,
        validation_harness=None,
        delivery_manager=None,
        maintenance_runner=None,
    ) -> None:
        self.llm = llm
        self.skills = skill_registry
        self.memory = memory
        self.system_prompt = system_prompt
        self.skill_prompt_mode = skill_prompt_mode
        self.max_history = max_history
        self.identities = identity_registry or IdentityRegistry()
        self.fact_extractor = fact_extractor
        self.fact_extraction_every_n_turns = fact_extraction_every_n_turns
        self.fact_extraction_min_confidence = fact_extraction_min_confidence
        self.self_evaluator = self_evaluator
        self.pattern_detector = pattern_detector
        self.skill_synthesizer = skill_synthesizer
        self.emergence_every_n_turns = emergence_every_n_turns
        self.emergence_min_confidence = emergence_min_confidence
        self.emergence_inline_timeout_s = emergence_inline_timeout_s
        self.title_generator = title_generator
        self.title_after_turns = title_after_turns
        self.tool_registry = tool_registry
        self.max_tool_iterations = max_tool_iterations
        self.skill_contract_auto_update = skill_contract_auto_update
        self.harness_config = harness_config
        self.base_dir = Path(base_dir) if base_dir is not None else None
        self.config_path = Path(config_path) if config_path is not None else None
        self.runtime_manager = runtime_manager
        self.validation_harness = validation_harness
        self.delivery_manager = delivery_manager
        self.maintenance_runner = maintenance_runner
        self._turn_counts: dict[str, int] = {}  # conversation_id → turn count
        self._emergence_counts: dict[str, int] = {}
        self._background_emergence_tasks: set[asyncio.Task] = set()

    def _tool_registry_for_turn(
        self,
        conversation_id: str,
        identity: Identity | None,
        mode: str,
    ) -> ToolRegistry | None:
        if self.tool_registry is None:
            return None

        registry = ToolRegistry()
        namespace = identity.namespace if identity is not None else ""
        registry.register_many(skill_tools(self.skills))
        registry.register_many(
            memory_tools(
                self.memory,
                namespace=namespace,
                allow_write=mode == "execute",
            )
        )
        registry.register_many(
            conversation_tools(self.memory, current_conversation_id=conversation_id)
        )
        registry.register_many(plan_tools(self.memory, current_conversation_id=conversation_id))
        if mode == "execute":
            registry.register_many(self.tool_registry.all())
        return registry

    async def _run_llm(
        self,
        messages: list[dict],
        tool_registry: ToolRegistry | None = None,
    ) -> tuple[str, list[dict]]:
        """Run the LLM, looping through tool calls until we get a final text.

        Returns (final_response_text, tool_trace) where tool_trace is a
        list of {name, arguments, result, is_error} entries recorded for
        the UI / memory.

        If no ToolRegistry is configured, falls back to a single
        plain-text completion (legacy path).
        """
        if not tool_registry or len(tool_registry) == 0:
            text = await self.llm.complete(messages)
            return text, []

        tools = tool_registry.all()
        tool_trace: list[dict] = []
        working: list[dict] = list(messages)

        for _ in range(self.max_tool_iterations):
            result = await self.llm.complete_with_tools(working, tools)
            if not result.tool_calls:
                return result.text, tool_trace

            # Record the assistant's tool-call turn in working messages
            # in OpenAI chat.completions format (CodexClient handles its
            # own conversion in _build_payload).
            working.append(
                {
                    "role": "assistant",
                    "content": result.text or None,
                    "tool_calls": [self._openai_tool_call(tc) for tc in result.tool_calls],
                    # Keep evidune-native tool_calls alongside so CodexClient
                    # can rebuild Responses API function_call items without
                    # re-parsing JSON
                    "_evidune_tool_calls": [
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in result.tool_calls
                    ],
                }
            )

            # Execute each call and append its result
            for tc in result.tool_calls:
                tr = await tool_registry.execute(tc)
                tool_trace.append(
                    {
                        "name": tc.name,
                        "arguments": tc.arguments,
                        "result": tr.content,
                        "is_error": tr.is_error,
                    }
                )
                working.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tr.content,
                    }
                )

        # Hit iteration cap — force a final text completion without tools
        final = await self.llm.complete(working)
        return final, tool_trace

    # Normalise messages so the assistant role message carrying tool_calls
    # serialises cleanly to OpenAI chat.completions (see ToolCall shape)
    def _openai_tool_call(self, tc: ToolCall) -> dict:
        import json as _json

        return {
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.name, "arguments": _json.dumps(tc.arguments)},
        }

    def _resolve_identity(
        self, message: InboundMessage, conversation_meta: dict | None = None
    ) -> Identity | None:
        """Pick the identity package for this turn.

        Priority: message.metadata['identity'] > stored conversation identity > registry default.
        Returns None if no identity packages are configured at all.
        """
        requested = message.metadata.get("identity") if message.metadata else None
        if requested:
            return self.identities.resolve(requested)

        stored = (conversation_meta or {}).get("identity")
        if stored:
            identity = self.identities.get(stored)
            if identity is not None:
                return identity

        return self.identities.resolve(None)

    def _resolve_mode(self, message: InboundMessage, conversation_meta: dict | None = None) -> str:
        """Pick the operating mode for this turn."""
        requested = message.metadata.get("mode") if message.metadata else None
        if requested in _CONVERSATION_MODES:
            return requested

        stored = (conversation_meta or {}).get("mode")
        if stored in _CONVERSATION_MODES:
            return stored

        return "execute"

    def _harness_value(self, key: str, default):
        if self.harness_config is None:
            return default
        return getattr(self.harness_config, key, default)

    def _use_swarm_harness(
        self,
        message: InboundMessage,
        mode: str,
        relevant_skills: list,
    ) -> bool:
        if self._harness_value("strategy", "single") != "swarm":
            return False
        if mode == "plan":
            return True
        lower = message.text.lower()
        if len(relevant_skills) > 1:
            return True
        if any(
            token in lower
            for token in (
                "plan",
                "step by step",
                "compare",
                "research",
                "debug",
                "implement",
                "build",
                "fix",
                "search",
                "analyze",
                "investigate",
                "write tests",
            )
        ):
            return True
        if self.tool_registry is not None and any(
            token in lower for token in ("file", "code", "test", "shell", "http", "run", "grep")
        ):
            return True
        return len(message.text.split()) > int(self._harness_value("simple_turn_threshold", 18))

    def _resolve_squad(
        self,
        message: InboundMessage,
        conversation_meta: dict | None,
        identity: Identity | None,
        relevant_skills: list,
    ):
        stored = (conversation_meta or {}).get(
            "squad_profile"
        ) or self.memory.get_conversation_squad_profile(message.conversation_id)
        if stored:
            return get_squad_profile(stored)

        lower = message.text.lower()
        if any(token in lower for token in ("research", "compare", "investigate", "survey")):
            return get_squad_profile("research")
        if any(token in lower for token in ("implement", "fix", "build", "run", "test", "debug")):
            return get_squad_profile("execution")
        if identity is not None and "research" in (identity.name or "").lower():
            return get_squad_profile("research")
        if len(relevant_skills) > 1:
            return get_squad_profile("research")
        return get_squad_profile(self._harness_value("default_squad", "general"))

    def _identity_prompt(self, identity: Identity | None) -> str:
        if identity is None or not identity.prompt:
            return ""
        return "\n".join(
            [f"# Identity Package: {identity.display_name or identity.name}", identity.prompt]
        )

    def _facts_payload(self, facts: list) -> list[dict[str, str]]:
        return [{"key": fact.key, "value": fact.value} for fact in facts]

    def _worker_skill_groups(self, relevant_skills: list, branches: int) -> list[list]:
        if branches <= 0:
            return []
        groups: list[list] = [[] for _ in range(branches)]
        if not relevant_skills:
            return groups
        for skill in relevant_skills[: branches * 2]:
            target = min(range(branches), key=lambda idx: len(groups[idx]))
            groups[target].append(skill)
        return groups

    def _swarm_tool_registries(
        self,
        task_id: str,
        conversation_id: str,
        identity: Identity | None,
        mode: str,
        environment=None,
    ) -> dict[str, ToolRegistry | None]:
        namespace = identity.namespace if identity is not None else ""
        from agent.tools.harness_tools import harness_tools

        def base_registry(*, allow_write: bool, include_external: bool, include_tools: bool = True):
            registry = ToolRegistry()
            if include_tools:
                registry.register_many(
                    memory_tools(
                        self.memory,
                        namespace=namespace,
                        allow_write=allow_write,
                    )
                )
                registry.register_many(
                    conversation_tools(self.memory, current_conversation_id=conversation_id)
                )
                registry.register_many(
                    plan_tools(self.memory, current_conversation_id=conversation_id)
                )
            if include_external and self.tool_registry is not None and mode == "execute":
                registry.register_many(self.tool_registry.all())
            if environment is not None:
                registry.register_many(
                    harness_tools(
                        memory=self.memory,
                        task_id=task_id,
                        environment=environment,
                        validator=self.validation_harness,
                        delivery_manager=self.delivery_manager,
                        maintenance_runner=self.maintenance_runner,
                        allow_mutation=allow_write,
                    )
                )
            return registry if len(registry) > 0 else None

        worker_write = mode == "execute"
        return {
            "planner": base_registry(allow_write=False, include_external=False),
            "worker": base_registry(
                allow_write=worker_write,
                include_external=worker_write,
            ),
            "critic": base_registry(allow_write=False, include_external=False),
            "synthesizer": None,
        }

    def _swarm_tool_trace(self, task_id: str) -> list[dict]:
        trace: list[dict] = []
        for step in self.memory.list_harness_steps(task_id):
            for item in step["tool_trace"]:
                trace.append(item)
        return trace

    def _sync_turn_counter(self, cache: dict[str, int], conversation_id: str) -> int:
        """Restore or advance a monotonic turn counter from persisted conversation state."""
        persisted = self.memory.get_conversation_turn_count(conversation_id)
        current = max(cache.get(conversation_id, 0), persisted)
        cache[conversation_id] = current
        return current

    def _log_emergence_turn(self, decision: EmergenceDecision) -> None:
        payload = {
            "event": "emergence_turn",
            "conversation_id": decision.conversation_id,
            "mode": decision.mode,
            "matched_skills": decision.matched_skills,
            "execution_skill_names": decision.execution_skill_names,
            "emergence_counter": decision.emergence_counter,
            "emergence_attempted": decision.emergence_attempted,
            "skip_reason": decision.skip_reason,
            "detected_name": decision.detected_name,
            "detected_confidence": decision.detected_confidence,
            "activation_status": decision.activation_status,
            "emerged_skill_path": decision.emerged_skill_path,
            "trigger_reason": decision.trigger_reason,
            "skill_snapshot_count": decision.skill_snapshot_count,
            "skill_match_reasons": decision.skill_match_reasons,
            "skill_prompt_token_estimate": decision.skill_prompt_token_estimate,
            "skill_creation_status": (decision.skill_creation or {}).get("status", ""),
            "resolver_action": decision.resolver_action,
            "duplicate_of": decision.duplicate_of,
            "load_error": decision.load_error,
            "evaluation_contract_status": decision.evaluation_contract_status,
            "aggregate_score": decision.aggregate_score,
            "lowest_criteria": decision.lowest_criteria,
            "observed_metric_count": decision.observed_metric_count,
            "evaluation_samples": decision.evaluation_samples,
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)

    def _attach_evaluation_summary(
        self,
        decision: EmergenceDecision,
        evaluations: list[dict[str, Any]],
    ) -> None:
        if not evaluations:
            return
        decision.evaluation_samples = len(evaluations)
        latest = evaluations[-1]
        decision.evaluation_contract_status = latest.get("contract_status", "")
        score = latest.get("aggregate_score")
        decision.aggregate_score = float(score) if score is not None else None
        criteria = latest.get("criteria_scores") or {}
        if criteria:
            lowest = min(criteria.items(), key=lambda item: item[1])
            decision.lowest_criteria = str(lowest[0])
        decision.observed_metric_count = len(latest.get("observed_metrics") or {})

    def _attach_skill_snapshot(self, decision: EmergenceDecision, snapshot) -> None:
        """Copy registry snapshot diagnostics onto an emergence decision."""
        log_data = snapshot.to_log_dict()
        decision.skill_snapshot_count = log_data["skill_snapshot_count"]
        decision.skill_match_reasons = log_data["skill_match_reasons"]
        decision.skill_prompt_token_estimate = log_data["skill_prompt_token_estimate"]

    def _skill_creation_response(self, decision: EmergenceDecision) -> str:
        creation = decision.skill_creation or {}
        status = creation.get("status") or "failed"
        name = creation.get("skill_name") or decision.detected_name or "unknown"
        reason = creation.get("reason") or decision.skip_reason or "unknown"

        if status == "created":
            return f"Skill `{name}` 已创建并激活。后续相似请求会优先匹配这个 skill。"
        if status == "updated":
            return f"Skill `{name}` 已更新并保持激活。没有创建重复 skill。"
        if status == "reused":
            return f"已复用现有 skill `{name}`，没有创建重复 skill。原因：{reason}"
        if status == "queued":
            if name == "unknown":
                return "Skill 创建已进入后台队列。完成后会写入 skill registry 并在后续请求中可用。"
            return f"Skill `{name}` 的创建已进入后台队列。完成后会写入 skill registry 并在后续请求中可用。"
        return f"Skill 创建失败：{reason}"

    def _failed_skill_creation(
        self,
        decision: EmergenceDecision,
        *,
        reason: str,
        status: str = "failed",
    ) -> EmergenceDecision:
        skill_name = decision.detected_name or ""
        decision.skill_creation = {
            "status": status,
            "skill_name": skill_name,
            "path": decision.emerged_skill_path,
            "files": [],
            "confidence": decision.detected_confidence,
            "reason": reason,
            "duplicate_of": decision.duplicate_of,
            "trigger_reason": decision.trigger_reason,
        }
        return decision

    def _track_background_emergence(
        self,
        task: asyncio.Task[EmergenceDecision],
        fallback: EmergenceDecision,
    ) -> None:
        self._background_emergence_tasks.add(task)

        def _done(done_task: asyncio.Task[EmergenceDecision]) -> None:
            self._background_emergence_tasks.discard(done_task)
            try:
                decision = done_task.result()
            except asyncio.CancelledError:
                fallback.skip_reason = "emergence_cancelled"
                fallback.activation_status = "failed"
                if fallback.trigger_reason == "explicit_skill_request":
                    self._failed_skill_creation(fallback, reason="emergence_cancelled")
                decision = fallback
            except Exception:
                fallback.skip_reason = "emergence_failed"
                fallback.activation_status = "failed"
                if fallback.trigger_reason == "explicit_skill_request":
                    self._failed_skill_creation(fallback, reason="emergence_failed")
                decision = fallback
            self._log_emergence_turn(decision)

        task.add_done_callback(_done)

    async def wait_for_background_emergence(
        self,
        timeout_s: float | None = None,
    ) -> list[EmergenceDecision]:
        """Wait for currently queued emergence attempts to finish.

        This is primarily useful for CLI/smoke flows that need to exit only
        after queued skill creation has had a chance to persist and register.
        Long-running serve processes do not need to call it.
        """
        if not self._background_emergence_tasks:
            return []

        tasks = set(self._background_emergence_tasks)
        done, _pending = await asyncio.wait(tasks, timeout=timeout_s)
        decisions: list[EmergenceDecision] = []
        for task in done:
            if task.cancelled():
                continue
            try:
                decisions.append(task.result())
            except Exception:
                continue
        return decisions

    async def _run_swarm(
        self,
        *,
        task_id: str,
        message: InboundMessage,
        identity: Identity | None,
        mode: str,
        facts: list,
        history: list[dict[str, str]],
        relevant_skills: list,
        squad,
    ):
        event_sink = message.metadata.get("event_sink") if message.metadata else None
        if not callable(event_sink) or not self._harness_value("stream_events", True):
            event_sink = None
        environment = (
            self.runtime_manager.create_environment(task_id) if self.runtime_manager else None
        )
        harness = SwarmHarness(
            llm=self.llm,
            memory=self.memory,
            system_prompt=self.system_prompt,
            max_tool_iterations=self.max_tool_iterations,
            max_rounds=int(self._harness_value("max_rounds", 2)),
            max_worker_branches=int(self._harness_value("max_worker_branches", 2)),
            token_budget=int(self._harness_value("token_budget", 20_000)),
            tool_call_budget=int(self._harness_value("tool_call_budget", 16)),
            wall_clock_budget_s=int(self._harness_value("wall_clock_budget_s", 120)),
        )
        return await harness.run(
            brief=TaskBrief(
                user_input=message.text,
                conversation_id=message.conversation_id,
                mode=mode,
                identity_name=identity.name if identity else "",
                history=history,
                facts=self._facts_payload(facts),
                selected_skills=[skill.name for skill in relevant_skills],
            ),
            squad=squad,
            task_id=task_id,
            environment=environment,
            identity_prompt=self._identity_prompt(identity),
            worker_skill_groups=self._worker_skill_groups(relevant_skills, squad.worker_branches),
            tool_registry_by_role=self._swarm_tool_registries(
                task_id, message.conversation_id, identity, mode, environment=environment
            ),
            event_sink=event_sink,
            surface="serve",
        )

    async def handle(self, message: InboundMessage) -> OutboundMessage:
        """Process an inbound message and return a response.

        Flow:
        1. Pick identity package (from message.metadata or registry default)
        2. Load conversation history from memory
        3. Load facts from the identity namespace (+ shared global)
        4. Find relevant skills
        5. Build prompt (identity + system + skills + facts + history + message)
        6. Call LLM
        7. Store message + response in memory
        8. Record skill executions
        """
        # Ensure the conversation exists with the originating gateway/channel
        # before any later reads or writes. Web UI lists are channel-scoped.
        self.memory.ensure_conversation(message.conversation_id, channel=message.channel)
        self._prune_inactive_skills()

        # 1. Identity + mode
        conversation_meta = self.memory.get_conversation(message.conversation_id)
        identity = self._resolve_identity(message, conversation_meta)
        mode = self._resolve_mode(message, conversation_meta)
        if identity is not None:
            self.memory.set_conversation_identity(message.conversation_id, identity.name)
        self.memory.set_conversation_mode(message.conversation_id, mode)

        # 2. History
        history = self.memory.get_history(message.conversation_id, self.max_history)

        # 3. Facts (identity-scoped + global)
        if identity is not None:
            identity_facts = self.memory.get_facts(namespace=identity.namespace)
            global_facts = self.memory.get_facts(namespace="")
            facts = global_facts + identity_facts
        else:
            facts = self.memory.get_facts()

        # 4. Relevant skills
        skill_snapshot = self.skills.snapshot(message.text)
        matched_skills = [match.skill for match in skill_snapshot.matches]
        relevant_skills = matched_skills if matched_skills else self.skills.all()
        execution_skills = matched_skills if matched_skills else []
        matched_skill_names = [skill.name for skill in matched_skills]
        execution_skill_names = [skill.name for skill in execution_skills]

        if (
            _is_explicit_skill_request(message.text)
            and self.pattern_detector
            and self.skill_synthesizer
        ):
            self.memory.add_message(message.conversation_id, "user", message.text)
            current_turn_count = self._sync_turn_counter(self._turn_counts, message.conversation_id)
            current_emergence_count = self._sync_turn_counter(
                self._emergence_counts, message.conversation_id
            )
            emergence_decision = await self._maybe_emerge_skill(
                message,
                mode=mode,
                matched_skill_names=matched_skill_names,
                execution_skill_names=[],
                emergence_counter=current_emergence_count,
            )
            self._attach_skill_snapshot(emergence_decision, skill_snapshot)
            response_text = self._skill_creation_response(emergence_decision)
            self.memory.add_message(message.conversation_id, "assistant", response_text)
            extracted_count = await self._maybe_extract_facts(
                message, identity, turn_count=current_turn_count
            )
            new_title = await self._maybe_generate_title(message.conversation_id)
            current_plan = self.memory.get_conversation_plan(message.conversation_id)
            self._log_emergence_turn(emergence_decision)
            self.memory.trim_history(message.conversation_id, keep=self.max_history * 5)
            return OutboundMessage(
                text=response_text,
                conversation_id=message.conversation_id,
                metadata={
                    "skills": [],
                    "execution_ids": [],
                    "identity": identity.name if identity else None,
                    "mode": mode,
                    "plan": current_plan,
                    "facts_extracted": extracted_count,
                    "evaluations_recorded": 0,
                    "skill_evaluations": [],
                    "emerged_skill": emergence_decision.emerged_skill,
                    "skill_creation": emergence_decision.skill_creation,
                    "skill_lifecycle_updates": [],
                    "new_title": new_title,
                    "tool_trace": [],
                    "task_id": None,
                    "squad": None,
                    "task_status": None,
                    "task_events": [],
                    "convergence_summary": None,
                    "budget_summary": None,
                    "environment_id": None,
                    "environment_status": None,
                    "validation_summary": None,
                    "delivery_summary": None,
                    "artifact_manifest": None,
                },
            )

        task_id: str | None = None
        squad_name: str | None = None
        task_status: str | None = None
        task_events: list[dict] = []
        convergence_summary: dict | None = None
        budget_summary: dict | None = None
        environment_id: str | None = None
        environment_status: str | None = None
        validation_summary: dict | None = None
        delivery_summary: dict | None = None
        artifact_manifest: dict | None = None
        if self._use_swarm_harness(message, mode, matched_skills):
            squad = self._resolve_squad(message, conversation_meta, identity, matched_skills)
            self.memory.save_squad_profile(
                squad.name,
                roles=squad.role_roster(),
                config=squad.to_dict(),
            )
            self.memory.set_conversation_squad_profile(message.conversation_id, squad.name)
            task_id = f"task-{uuid.uuid4().hex[:10]}"
            swarm_task = await self._run_swarm(
                task_id=task_id,
                message=message,
                identity=identity,
                mode=mode,
                facts=facts,
                history=history,
                relevant_skills=matched_skills,
                squad=squad,
            )
            response_text = swarm_task.final_output
            task_id = swarm_task.id
            squad_name = swarm_task.squad.name
            task_status = swarm_task.status
            task_events = [event.to_dict() for event in swarm_task.events]
            convergence_summary = swarm_task.convergence_summary
            budget_summary = swarm_task.budget_summary.to_dict()
            environment_id = swarm_task.environment_id or None
            environment_status = swarm_task.environment_status or None
            validation_summary = swarm_task.validation_summary or None
            delivery_summary = swarm_task.delivery_summary or None
            artifact_manifest = swarm_task.artifact_manifest or None
            tool_trace = self._swarm_tool_trace(swarm_task.id)
        else:
            # 5. Build messages
            messages = self._build_messages(
                identity, mode, relevant_skills, facts, history, message
            )

            # 6. Call LLM — with optional tool-use loop
            turn_tools = self._tool_registry_for_turn(message.conversation_id, identity, mode)
            response_text, tool_trace = await self._run_llm(messages, tool_registry=turn_tools)

        # 7. Store in memory (only user input + final assistant response;
        #    intermediate tool calls stay in the per-turn working messages)
        self.memory.add_message(message.conversation_id, "user", message.text)
        self.memory.add_message(message.conversation_id, "assistant", response_text)
        current_turn_count = self._sync_turn_counter(self._turn_counts, message.conversation_id)
        current_emergence_count = self._sync_turn_counter(
            self._emergence_counts, message.conversation_id
        )

        # 8. Record skill execution(s) for outcome iteration / feedback
        execution_ids: list[int] = []
        for skill in execution_skills:
            origin = self._skill_origin(skill.name)
            self.memory.upsert_skill_state(
                skill.name,
                origin=origin,
                path=str(skill.path),
                status=self.memory.resolve_skill_status(skill.name),
            )
            eid = self.memory.record_execution(
                skill_name=skill.name,
                user_input=message.text,
                assistant_output=response_text,
                conversation_id=message.conversation_id,
                harness_task_id=task_id,
            )
            execution_ids.append(eid)
        skill_evaluations = await self._maybe_evaluate_executions(
            execution_skills,
            message.text,
            response_text,
            execution_ids,
            tool_trace=tool_trace,
        )
        lifecycle_updates = self._maybe_reconcile_skill_feedback(execution_skills)

        # 9. Auto fact extraction (every N turns)
        extracted_count = await self._maybe_extract_facts(
            message, identity, turn_count=current_turn_count
        )

        # 10. Skill emergence: explicit creation requests bypass cadence.
        emergence_decision = await self._maybe_emerge_skill(
            message,
            mode=mode,
            matched_skill_names=matched_skill_names,
            execution_skill_names=execution_skill_names,
            emergence_counter=current_emergence_count,
        )
        self._attach_skill_snapshot(emergence_decision, skill_snapshot)
        self._attach_evaluation_summary(emergence_decision, skill_evaluations)

        # 11. Auto-title the conversation once it has enough content
        new_title = await self._maybe_generate_title(message.conversation_id)

        current_plan = self.memory.get_conversation_plan(message.conversation_id)
        self._log_emergence_turn(emergence_decision)

        # Trim old history
        self.memory.trim_history(message.conversation_id, keep=self.max_history * 5)

        return OutboundMessage(
            text=response_text,
            conversation_id=message.conversation_id,
            metadata={
                "skills": execution_skill_names,
                "execution_ids": execution_ids,
                "identity": identity.name if identity else None,
                "mode": mode,
                "plan": current_plan,
                "facts_extracted": extracted_count,
                "evaluations_recorded": len(skill_evaluations),
                "skill_evaluations": skill_evaluations,
                "emerged_skill": emergence_decision.emerged_skill,
                "skill_creation": emergence_decision.skill_creation,
                "skill_lifecycle_updates": lifecycle_updates,
                "new_title": new_title,
                "tool_trace": tool_trace,
                "task_id": task_id,
                "squad": squad_name,
                "task_status": task_status,
                "task_events": task_events,
                "convergence_summary": convergence_summary,
                "budget_summary": budget_summary,
                "environment_id": environment_id,
                "environment_status": environment_status,
                "validation_summary": validation_summary,
                "delivery_summary": delivery_summary,
                "artifact_manifest": artifact_manifest,
            },
        )

    async def _maybe_extract_facts(
        self,
        message: InboundMessage,
        identity: Identity | None,
        *,
        turn_count: int | None = None,
    ) -> int:
        """Extract new facts from history every N turns. Returns count saved."""
        if not self.fact_extractor:
            return 0

        conv_id = message.conversation_id
        current_turn_count = (
            turn_count
            if turn_count is not None
            else self._sync_turn_counter(self._turn_counts, conv_id)
        )
        if current_turn_count % self.fact_extraction_every_n_turns != 0:
            return 0

        namespace = identity.namespace if identity else ""
        history = self.memory.get_history(conv_id, self.max_history)
        existing = self.memory.get_facts(namespace=namespace) + (
            self.memory.get_facts(namespace="") if namespace else []
        )

        try:
            candidates = await self.fact_extractor.extract(history, existing_facts=existing)
        except Exception:
            return 0

        saved = 0
        for c in candidates:
            if c.confidence < self.fact_extraction_min_confidence:
                continue
            self.memory.set_fact(c.key, c.value, source="auto", namespace=namespace)
            saved += 1
        return saved

    async def _maybe_evaluate_executions(
        self,
        skills: list,
        user_input: str,
        assistant_output: str,
        execution_ids: list[int],
        *,
        tool_trace: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Persist cross-model evaluation scores for matched skill executions."""
        if not self.self_evaluator:
            return []

        saved: list[dict[str, Any]] = []
        for skill, execution_id in zip(skills, execution_ids, strict=False):
            contract_status = await self._ensure_skill_evaluation_contract(
                skill,
                user_input=user_input,
                assistant_output=assistant_output,
            )
            try:
                evaluation = await self.self_evaluator.evaluate(
                    skill,
                    user_input,
                    assistant_output,
                    tool_trace=tool_trace,
                )
            except Exception:
                continue
            if self.memory.update_execution_score(
                execution_id,
                evaluation.score,
                evaluation.reasoning,
            ):
                self.memory.record_skill_evaluation(
                    execution_id=execution_id,
                    skill_name=skill.name,
                    aggregate_score=evaluation.score,
                    criteria_scores=evaluation.criteria_scores,
                    observed_metrics=evaluation.observed_metrics,
                    missing_observations=evaluation.missing_observations,
                    reasoning=evaluation.reasoning,
                    contract_version=evaluation.contract_version,
                )
                saved.append(
                    {
                        "skill_name": skill.name,
                        "execution_id": execution_id,
                        "contract_status": contract_status,
                        "aggregate_score": evaluation.score,
                        "criteria_scores": evaluation.criteria_scores or {},
                        "observed_metrics": evaluation.observed_metrics or {},
                        "missing_observations": evaluation.missing_observations or [],
                        "reasoning": evaluation.reasoning,
                    }
                )
        return saved

    async def _ensure_skill_evaluation_contract(
        self,
        skill: Skill,
        *,
        user_input: str,
        assistant_output: str,
    ) -> str:
        """Ensure a matched skill has an active evaluation contract."""
        if skill.evaluation_contract is not None:
            self.memory.upsert_skill_evaluation_contract(
                skill.name,
                skill.evaluation_contract.to_dict(),
                source="skill",
                path=str(skill.path),
                reason="Loaded from SKILL.md frontmatter",
            )
            return "skill"

        stored = self.memory.get_skill_evaluation_contract(skill.name)
        if stored is not None:
            contract = parse_evaluation_contract(stored.get("contract"))
            if contract is not None:
                skill.evaluation_contract = contract
                self.skills.register(
                    skill,
                    source=self._skill_origin(skill.name),
                    status=self.memory.resolve_skill_status(skill.name),
                )
                return stored.get("source") or "runtime"

        if self.self_evaluator is None:
            return "missing"

        contract = await self.self_evaluator.discover_contract(
            skill,
            user_input=user_input,
            assistant_output=assistant_output,
        )
        skill.evaluation_contract = contract
        status = "runtime"
        content_before = ""
        content_after = ""
        if self.skill_contract_auto_update and skill.path.is_file():
            try:
                content_before = skill.path.read_text(encoding="utf-8")
                content_after = upsert_contract_frontmatter(content_before, contract)
                if content_after != content_before:
                    skill.path.write_text(content_after, encoding="utf-8")
                    status = "written"
                    self.memory.record_skill_lifecycle_event(
                        skill.name,
                        "contract_discover",
                        status=self.memory.resolve_skill_status(skill.name),
                        path=str(skill.path),
                        harness_task_id="",
                        reason="Discovered skill-specific evaluation contract",
                        evidence={"contract": contract_summary(contract)},
                        content_before=content_before,
                        content_after=content_after,
                    )
            except OSError:
                status = "runtime"
        self.memory.upsert_skill_evaluation_contract(
            skill.name,
            contract.to_dict(),
            source=status,
            path=str(skill.path),
            reason="Discovered skill-specific evaluation contract",
            evidence={"contract": contract_summary(contract)},
        )
        self.skills.register(
            skill,
            source=self._skill_origin(skill.name),
            status=self.memory.resolve_skill_status(skill.name),
        )
        return status

    def _skill_origin(self, skill_name: str) -> str:
        state = self.memory.get_skill_state(skill_name)
        if state is not None:
            return state["origin"]
        return "emerged" if self.memory.get_emerged_skill(skill_name) else "base"

    def _prune_inactive_skills(self) -> None:
        """Drop any non-active skill from the live registry."""
        for status in ("pending_review", "disabled", "rolled_back"):
            for skill_meta in self.memory.list_skill_states(status=status):
                self.skills.unregister(skill_meta["skill_name"])

    def _resolve_skill_creation_target(
        self,
        pattern,
        history: list[dict[str, str]],
    ) -> tuple[str, Skill | None, SkillMatch | None]:
        """Return create/update/reuse for a detected skill candidate."""
        source_text = " ".join(item.get("content", "") for item in history[-8:])
        matches = self.skills.find_similar(
            name=pattern.suggested_name,
            text=f"{pattern.description} {pattern.rationale} {source_text}",
            max_results=1,
        )
        if not matches:
            return "create", None, None

        match = matches[0]
        origin = self._skill_origin(match.skill.name)
        if origin == "emerged":
            return "update", match.skill, match
        return "reuse", match.skill, match

    def _write_synthesis_bundle(self, result) -> None:
        """Persist a synthesised safe markdown bundle to its target directory."""
        skill_dir = result.path.parent
        for rel_path, content in result.files.items():
            target = skill_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content.strip() + "\n", encoding="utf-8")

    def _parse_synthesis_bundle_in_temp(self, result) -> Skill:
        """Validate a bundle before overwriting an existing active skill."""
        with tempfile.TemporaryDirectory(prefix="evidune-skill-") as temp:
            skill_dir = Path(temp) / result.name
            for rel_path, content in result.files.items():
                target = skill_dir / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content.strip() + "\n", encoding="utf-8")
            return parse_skill(skill_dir / "SKILL.md")

    def _set_skill_creation(
        self,
        decision: EmergenceDecision,
        *,
        status: str,
        skill_name: str,
        reason: str,
        path: str = "",
        files: list[str] | None = None,
        duplicate_of: str = "",
    ) -> None:
        decision.skill_creation = {
            "status": status,
            "skill_name": skill_name,
            "path": path,
            "files": files or [],
            "confidence": decision.detected_confidence,
            "reason": reason,
            "duplicate_of": duplicate_of,
            "trigger_reason": decision.trigger_reason,
        }

    def _maybe_reconcile_skill_feedback(self, skills: list) -> list[str]:
        """Use stored signals and evaluator scores through the shared governance harness."""
        updated: list[str] = []
        seen: set[str] = set()

        for skill in skills:
            if skill.name in seen:
                continue
            seen.add(skill.name)
            if self.memory.resolve_skill_status(skill.name) != "active":
                continue

            current = skill.path.read_text(encoding="utf-8")
            packet = build_decision_packet(
                self.memory,
                skill=skill,
                current=current,
                result=None,
                surface="serve",
                task_kind="skill_feedback",
            )
            summary = packet.feedback
            if summary is None or (summary.signal_samples == 0 and summary.score_samples == 0):
                continue
            workflow = IterationHarness(self.memory)
            decision = workflow.run(packet=packet)
            if decision.decision in {"rollback", "disable"}:
                self.skills.unregister(skill.name)
                updated.append(skill.name)

        return updated

    async def _maybe_emerge_skill(
        self,
        message: InboundMessage,
        *,
        mode: str,
        matched_skill_names: list[str],
        execution_skill_names: list[str],
        emergence_counter: int | None = None,
    ) -> EmergenceDecision:
        """Detect and synthesise a new skill from recent conversation."""
        conv_id = message.conversation_id
        current_counter = (
            emergence_counter
            if emergence_counter is not None
            else self._sync_turn_counter(self._emergence_counts, conv_id)
        )
        decision = EmergenceDecision(
            conversation_id=conv_id,
            mode=mode,
            matched_skills=matched_skill_names,
            execution_skill_names=execution_skill_names,
            emergence_counter=current_counter,
        )

        explicit_request = _is_explicit_skill_request(message.text)

        if not (self.pattern_detector and self.skill_synthesizer):
            decision.skip_reason = "disabled_by_config"
            if explicit_request:
                decision.trigger_reason = "explicit_skill_request"
                decision.activation_status = "failed"
                self._failed_skill_creation(
                    decision, reason="skill emergence subsystem unavailable"
                )
            return decision

        due_by_cadence = (
            self.emergence_every_n_turns > 0 and current_counter % self.emergence_every_n_turns == 0
        )
        if not (explicit_request or due_by_cadence):
            decision.skip_reason = "not_due_yet"
            return decision

        decision.trigger_reason = "explicit_skill_request" if explicit_request else "cadence"
        attempt_decision = EmergenceDecision(
            conversation_id=conv_id,
            mode=mode,
            matched_skills=matched_skill_names,
            execution_skill_names=execution_skill_names,
            emergence_counter=current_counter,
            trigger_reason="explicit_skill_request" if explicit_request else "cadence",
        )
        task = asyncio.create_task(self._run_emergence_attempt(attempt_decision, conv_id))
        try:
            return await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self.emergence_inline_timeout_s,
            )
        except asyncio.TimeoutError:
            self._track_background_emergence(task, attempt_decision)
            decision.emergence_attempted = True
            decision.skip_reason = "emergence_queued"
            decision.activation_status = "pending"
            decision.trigger_reason = attempt_decision.trigger_reason
            decision.skill_creation = {
                "status": "queued",
                "skill_name": "",
                "path": "",
                "files": [],
                "confidence": None,
                "reason": "emergence attempt is running in the background",
                "duplicate_of": "",
                "trigger_reason": attempt_decision.trigger_reason,
            }
            return decision

    async def _run_emergence_attempt(
        self,
        decision: EmergenceDecision,
        conv_id: str,
    ) -> EmergenceDecision:
        decision.emergence_attempted = True
        history = self.memory.get_history(conv_id, self.max_history)
        existing_names = [s.name for s in self.skills.all()]

        try:
            pattern = await self.pattern_detector.detect(
                history, existing_skill_names=existing_names
            )
        except Exception:
            decision.skip_reason = "detector_failed"
            decision.activation_status = "failed"
            if decision.trigger_reason == "explicit_skill_request":
                self._failed_skill_creation(decision, reason="detector_failed")
            return decision

        decision.detected_name = pattern.suggested_name
        decision.detected_confidence = pattern.confidence

        if not pattern.is_skill or pattern.confidence < self.emergence_min_confidence:
            decision.skip_reason = "below_threshold"
            if decision.trigger_reason == "explicit_skill_request":
                self._failed_skill_creation(decision, reason="below_threshold")
            return decision

        resolver_action, existing_skill, match = self._resolve_skill_creation_target(
            pattern, history
        )
        decision.resolver_action = resolver_action
        if match is not None:
            decision.duplicate_of = match.skill.name

        if resolver_action == "reuse" and existing_skill is not None:
            decision.skip_reason = "duplicate_name"
            decision.activation_status = "reused"
            decision.emerged_skill_path = str(existing_skill.path)
            reason = (
                "Existing base/project skill already covers this capability "
                f"(score={match.score if match else 0})."
            )
            self._set_skill_creation(
                decision,
                status="reused",
                skill_name=existing_skill.name,
                path=str(existing_skill.path),
                reason=reason,
                duplicate_of=existing_skill.name,
            )
            return decision

        try:
            result = await self.skill_synthesizer.synthesize(
                pattern,
                history,
                write=resolver_action != "update",
                existing_skill=existing_skill if resolver_action == "update" else None,
            )
        except Exception:
            decision.skip_reason = "synthesis_failed"
            decision.activation_status = "failed"
            if decision.trigger_reason == "explicit_skill_request":
                self._failed_skill_creation(decision, reason="synthesis_failed")
            return decision
        if result is None:
            decision.skip_reason = "synthesis_failed"
            decision.activation_status = "failed"
            if decision.trigger_reason == "explicit_skill_request":
                self._failed_skill_creation(decision, reason="synthesis_failed")
            return decision

        decision.emerged_skill_path = str(result.path)
        content_before = ""

        try:
            if resolver_action == "update":
                if existing_skill is not None and existing_skill.path.is_file():
                    content_before = existing_skill.path.read_text(encoding="utf-8")
                self._parse_synthesis_bundle_in_temp(result)
                self._write_synthesis_bundle(result)
            skill = parse_skill(result.path)
        except Exception:
            decision.skip_reason = "parse_failed"
            decision.activation_status = "failed"
            decision.load_error = "Synthesised skill could not be parsed for activation"
            reason = "Synthesised skill could not be parsed for activation"
            evidence = {
                "pattern_confidence": pattern.confidence,
                "pattern_rationale": pattern.rationale,
            }
            if resolver_action != "update":
                self.memory.register_emerged_skill(
                    name=result.name,
                    source_conversation_id=conv_id,
                    evaluation_criteria=pattern.rationale,
                    status="disabled",
                    path=str(result.path),
                    reason=reason,
                    evidence=evidence,
                )
                self.memory.record_skill_lifecycle_event(
                    result.name,
                    "activation_failed",
                    status="disabled",
                    path=str(result.path),
                    harness_task_id="",
                    reason=reason,
                    evidence=evidence,
                    content_after=result.skill_md,
                )
            if decision.trigger_reason == "explicit_skill_request":
                self._failed_skill_creation(decision, reason="parse_failed")
            return decision

        evidence = {
            "pattern_confidence": pattern.confidence,
            "pattern_rationale": pattern.rationale,
            "pattern_description": pattern.description,
        }
        action = "update" if resolver_action == "update" else "activate"
        status_reason = (
            "Updated active emerged skill from explicit skill transaction"
            if resolver_action == "update"
            else "Auto-activated from conversation pattern"
        )
        try:
            self.memory.register_emerged_skill(
                name=result.name,
                source_conversation_id=conv_id,
                evaluation_criteria=pattern.rationale,
                status="active",
                path=str(result.path),
                reason=status_reason,
                evidence=evidence,
            )
            self.memory.record_skill_lifecycle_event(
                result.name,
                action,
                status="active",
                path=str(result.path),
                harness_task_id="",
                reason=status_reason,
                evidence=evidence,
                content_before=content_before,
                content_after=result.skill_md,
            )
            self.skills.register(skill, source="emerged")
        except Exception:
            decision.skip_reason = "activation_failed"
            decision.activation_status = "failed"
            if decision.trigger_reason == "explicit_skill_request":
                self._failed_skill_creation(decision, reason="activation_failed")
            return decision

        decision.activation_status = "updated" if resolver_action == "update" else "activated"
        decision.emerged_skill = result.name
        self._set_skill_creation(
            decision,
            status="updated" if resolver_action == "update" else "created",
            skill_name=result.name,
            path=str(result.path),
            files=sorted(result.files.keys()),
            reason=status_reason,
            duplicate_of=decision.duplicate_of,
        )
        return decision

    async def _maybe_generate_title(self, conversation_id: str) -> str | None:
        """Generate a title once the conversation has enough content.

        Skipped when:
        - no TitleGenerator is configured
        - the conversation already has a non-empty title
        - the conversation has fewer than `title_after_turns * 2` messages
          (counting both roles; so the default 3 means 6+ messages)

        Returns the new title on success, else None.
        """
        if not self.title_generator:
            return None

        meta = self.memory.get_conversation(conversation_id)
        if not meta or (meta.get("title") or "").strip():
            return None

        history = self.memory.get_history(conversation_id, limit=20)
        if len(history) < self.title_after_turns * 2:
            return None

        try:
            title = await self.title_generator.generate(history)
        except Exception:
            return None
        if not title:
            return None
        self.memory.set_conversation_title(conversation_id, title)
        return title

    def _build_messages(
        self,
        identity: Identity | None,
        mode: str,
        skills: list,
        facts: list,
        history: list[dict[str, str]],
        message: InboundMessage,
    ) -> list[dict[str, str]]:
        """Build the message list for the LLM call."""
        system_parts = []

        # Identity package comes FIRST — it defines the assistant.
        if identity is not None and identity.prompt:
            system_parts.append(f"# Identity Package: {identity.display_name or identity.name}")
            system_parts.append(identity.prompt)

        if self.system_prompt:
            system_parts.append(self.system_prompt)

        if mode == "plan":
            system_parts.append(
                "\n".join(
                    [
                        "# Operating Mode: Plan",
                        "You are in plan mode.",
                        "Focus on analysis, sequencing, risks, and a concrete next-step plan.",
                        "Do not claim work is executed when it has not been executed.",
                        "Use update_plan to keep the structured plan current.",
                        "Do not use execution-only tools in this mode.",
                    ]
                )
            )
        else:
            system_parts.append(
                "\n".join(
                    [
                        "# Operating Mode: Execute",
                        "You are in execute mode.",
                        "Carry out the requested work when feasible instead of stopping at analysis.",
                        "Use update_plan to keep the structured plan in sync with actual progress.",
                    ]
                )
            )

        # Inject skills
        skill_prompt = self._build_skill_prompt(skills)
        if skill_prompt:
            system_parts.append(skill_prompt)

        # Inject facts
        if facts:
            fact_lines = ["# Memory", ""]
            for f in facts:
                fact_lines.append(f"- **{f.key}**: {f.value}")
            system_parts.append("\n".join(fact_lines))

        messages: list[dict[str, str]] = []
        if system_parts:
            messages.append({"role": "system", "content": "\n\n".join(system_parts)})

        # Conversation history
        messages.extend(history)

        # Current message
        messages.append({"role": "user", "content": message.text})

        return messages

    def _build_skill_prompt(self, skills: list) -> str:
        """Render the skill prompt according to the configured disclosure mode."""
        mode = self.skill_prompt_mode
        if mode == "auto":
            mode = "index" if self.tool_registry is not None else "full"
        if mode == "index":
            return self.skills.as_index_prompt(skills)
        return self.skills.as_full_prompt(skills)
