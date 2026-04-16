"""Agent core — orchestrates skills, memory, identity, and LLM."""

from __future__ import annotations

from agent.fact_extractor import FactExtractor
from agent.llm import LLMClient
from agent.pattern_detector import PatternDetector
from agent.self_evaluator import SelfEvaluator
from agent.skill_feedback import summarise_skill_feedback
from agent.skill_synthesizer import SkillSynthesizer
from agent.title_generator import TitleGenerator
from agent.tools.base import ToolCall
from agent.tools.internal import conversation_tools, memory_tools, plan_tools, skill_tools
from agent.tools.registry import ToolRegistry
from gateway.base import InboundMessage, OutboundMessage
from identities.loader import Identity
from identities.registry import IdentityRegistry
from memory.store import MemoryStore
from skills.loader import parse_skill
from skills.registry import SkillRegistry

_CONVERSATION_MODES = {"plan", "execute"}


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
        title_generator: TitleGenerator | None = None,
        title_after_turns: int = 3,
        tool_registry: ToolRegistry | None = None,
        max_tool_iterations: int = 8,
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
        self.title_generator = title_generator
        self.title_after_turns = title_after_turns
        self.tool_registry = tool_registry
        self.max_tool_iterations = max_tool_iterations
        self._turn_counts: dict[str, int] = {}  # conversation_id → turn count
        self._emergence_counts: dict[str, int] = {}

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
                    # Keep aiflay-native tool_calls alongside so CodexClient
                    # can rebuild Responses API function_call items without
                    # re-parsing JSON
                    "_aiflay_tool_calls": [
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
        self._prune_inactive_emerged_skills()

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
        relevant_skills = self.skills.find_relevant(message.text)
        used_skill_fallback = not relevant_skills
        if used_skill_fallback:
            relevant_skills = self.skills.all()

        # 5. Build messages
        messages = self._build_messages(identity, mode, relevant_skills, facts, history, message)

        # 6. Call LLM — with optional tool-use loop
        turn_tools = self._tool_registry_for_turn(message.conversation_id, identity, mode)
        response_text, tool_trace = await self._run_llm(messages, tool_registry=turn_tools)

        # 7. Store in memory (only user input + final assistant response;
        #    intermediate tool calls stay in the per-turn working messages)
        self.memory.add_message(message.conversation_id, "user", message.text)
        self.memory.add_message(message.conversation_id, "assistant", response_text)

        # 8. Record skill execution(s) for outcome iteration / feedback
        execution_ids: list[int] = []
        for skill in relevant_skills:
            eid = self.memory.record_execution(
                skill_name=skill.name,
                user_input=message.text,
                assistant_output=response_text,
                conversation_id=message.conversation_id,
            )
            execution_ids.append(eid)
        evaluated_count = await self._maybe_evaluate_executions(
            relevant_skills if not used_skill_fallback else [],
            message.text,
            response_text,
            execution_ids,
        )
        lifecycle_updates = self._maybe_reconcile_skill_feedback(relevant_skills)

        # 9. Auto fact extraction (every N turns, when enabled)
        extracted_count = await self._maybe_extract_facts(message, identity)

        # 10. Skill emergence (every N turns, when enabled)
        emerged_skill = await self._maybe_emerge_skill(message)

        # 11. Auto-title the conversation once it has enough content
        new_title = await self._maybe_generate_title(message.conversation_id)

        current_plan = self.memory.get_conversation_plan(message.conversation_id)

        # Trim old history
        self.memory.trim_history(message.conversation_id, keep=self.max_history * 5)

        return OutboundMessage(
            text=response_text,
            conversation_id=message.conversation_id,
            metadata={
                "skills": [s.name for s in relevant_skills],
                "execution_ids": execution_ids,
                "identity": identity.name if identity else None,
                "mode": mode,
                "plan": current_plan,
                "facts_extracted": extracted_count,
                "evaluations_recorded": evaluated_count,
                "emerged_skill": emerged_skill,
                "skill_lifecycle_updates": lifecycle_updates,
                "new_title": new_title,
                "tool_trace": tool_trace,
            },
        )

    async def _maybe_extract_facts(self, message: InboundMessage, identity: Identity | None) -> int:
        """Extract new facts from history every N turns. Returns count saved."""
        if not self.fact_extractor:
            return 0

        conv_id = message.conversation_id
        self._turn_counts[conv_id] = self._turn_counts.get(conv_id, 0) + 1
        if self._turn_counts[conv_id] % self.fact_extraction_every_n_turns != 0:
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
    ) -> int:
        """Persist cross-model evaluation scores for matched skill executions."""
        if not self.self_evaluator:
            return 0

        saved = 0
        for skill, execution_id in zip(skills, execution_ids, strict=False):
            try:
                evaluation = await self.self_evaluator.evaluate(
                    skill,
                    user_input,
                    assistant_output,
                )
            except Exception:
                continue
            if self.memory.update_execution_score(
                execution_id,
                evaluation.score,
                evaluation.reasoning,
            ):
                saved += 1
        return saved

    def _prune_inactive_emerged_skills(self) -> None:
        """Drop disabled or rolled-back emerged skills from the live registry."""
        inactive = self.memory.list_emerged_skills(
            status="disabled"
        ) + self.memory.list_emerged_skills(status="rolled_back")
        for skill_meta in inactive:
            self.skills.unregister(skill_meta["name"])

    def _maybe_reconcile_skill_feedback(self, skills: list) -> list[str]:
        """Use stored signals and evaluator scores to retire bad emerged skills."""
        updated: list[str] = []
        seen: set[str] = set()

        for skill in skills:
            if skill.name in seen:
                continue
            seen.add(skill.name)

            emerged = self.memory.get_emerged_skill(skill.name)
            if not emerged or emerged.get("status") != "active":
                continue

            summary = summarise_skill_feedback(
                self.memory.get_skill_executions(skill.name, limit=20)
            )
            if not summary.should_disable:
                continue

            reason = "Automatic rollback after negative feedback or evaluator score"
            self.memory.set_emerged_skill_status(
                skill.name,
                "rolled_back",
                reason=reason,
                evidence=summary.evidence,
            )
            self.memory.record_skill_lifecycle_event(
                skill.name,
                "rollback",
                status="rolled_back",
                path=emerged.get("path", ""),
                reason=reason,
                evidence=summary.evidence,
            )
            self.skills.unregister(skill.name)
            updated.append(skill.name)

        return updated

    async def _maybe_emerge_skill(self, message: InboundMessage) -> str | None:
        """Detect and synthesise a new skill from recent conversation.

        Returns the new skill name if one was created, else None.
        Skips if pattern_detector or skill_synthesizer are not configured,
        or if the proposed name collides with an existing skill.
        """
        if not (self.pattern_detector and self.skill_synthesizer):
            return None

        conv_id = message.conversation_id
        self._emergence_counts[conv_id] = self._emergence_counts.get(conv_id, 0) + 1
        if self._emergence_counts[conv_id] % self.emergence_every_n_turns != 0:
            return None

        history = self.memory.get_history(conv_id, self.max_history)
        existing_names = [s.name for s in self.skills.all()]

        try:
            pattern = await self.pattern_detector.detect(
                history, existing_skill_names=existing_names
            )
        except Exception:
            return None

        if not pattern.is_skill or pattern.confidence < self.emergence_min_confidence:
            return None
        if pattern.suggested_name in existing_names:
            return None  # Avoid duplicate

        try:
            result = await self.skill_synthesizer.synthesize(pattern, history)
        except Exception:
            return None
        if result is None:
            return None

        try:
            skill = parse_skill(result.path)
        except Exception:
            reason = "Synthesised skill could not be parsed for activation"
            evidence = {
                "pattern_confidence": pattern.confidence,
                "pattern_rationale": pattern.rationale,
            }
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
                reason=reason,
                evidence=evidence,
                content_after=result.skill_md,
            )
            return None

        evidence = {
            "pattern_confidence": pattern.confidence,
            "pattern_rationale": pattern.rationale,
            "pattern_description": pattern.description,
        }
        self.memory.register_emerged_skill(
            name=result.name,
            source_conversation_id=conv_id,
            evaluation_criteria=pattern.rationale,
            status="active",
            path=str(result.path),
            reason="Auto-activated from conversation pattern",
            evidence=evidence,
        )
        self.memory.record_skill_lifecycle_event(
            result.name,
            "activate",
            status="active",
            path=str(result.path),
            reason="Auto-activated from conversation pattern",
            evidence=evidence,
            content_after=result.skill_md,
        )
        self.skills.register(skill)

        return result.name

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
