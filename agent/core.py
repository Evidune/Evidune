"""Agent core — orchestrates skills, memory, persona, and LLM."""

from __future__ import annotations

from agent.fact_extractor import FactExtractor
from agent.llm import LLMClient
from agent.pattern_detector import PatternDetector
from agent.skill_synthesizer import SkillSynthesizer
from agent.title_generator import TitleGenerator
from agent.tools.base import ToolCall
from agent.tools.registry import ToolRegistry
from gateway.base import InboundMessage, OutboundMessage
from memory.store import MemoryStore
from personas.loader import Persona
from personas.registry import PersonaRegistry
from skills.loader import parse_skill
from skills.registry import SkillRegistry


class AgentCore:
    """Central agent that handles messages using skills + memory + persona + LLM."""

    def __init__(
        self,
        llm: LLMClient,
        skill_registry: SkillRegistry,
        memory: MemoryStore,
        system_prompt: str = "",
        skill_prompt_mode: str = "auto",
        max_history: int = 20,
        persona_registry: PersonaRegistry | None = None,
        fact_extractor: FactExtractor | None = None,
        fact_extraction_every_n_turns: int = 5,
        fact_extraction_min_confidence: float = 0.7,
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
        self.personas = persona_registry or PersonaRegistry()
        self.fact_extractor = fact_extractor
        self.fact_extraction_every_n_turns = fact_extraction_every_n_turns
        self.fact_extraction_min_confidence = fact_extraction_min_confidence
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

    async def _run_llm(self, messages: list[dict]) -> tuple[str, list[dict]]:
        """Run the LLM, looping through tool calls until we get a final text.

        Returns (final_response_text, tool_trace) where tool_trace is a
        list of {name, arguments, result, is_error} entries recorded for
        the UI / memory.

        If no ToolRegistry is configured, falls back to a single
        plain-text completion (legacy path).
        """
        if not self.tool_registry or len(self.tool_registry) == 0:
            text = await self.llm.complete(messages)
            return text, []

        tools = self.tool_registry.all()
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
                tr = await self.tool_registry.execute(tc)
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

    def _resolve_persona(self, message: InboundMessage) -> Persona | None:
        """Pick the persona for this turn.

        Priority: message.metadata['persona'] > registry default.
        Returns None if no personas are configured at all.
        """
        requested = message.metadata.get("persona") if message.metadata else None
        return self.personas.resolve(requested)

    async def handle(self, message: InboundMessage) -> OutboundMessage:
        """Process an inbound message and return a response.

        Flow:
        1. Pick persona (from message.metadata or registry default)
        2. Load conversation history from memory
        3. Load facts from persona's namespace (+ shared global)
        4. Find relevant skills
        5. Build prompt (persona + system + skills + facts + history + message)
        6. Call LLM
        7. Store message + response in memory
        8. Record skill executions
        """
        # 1. Persona
        persona = self._resolve_persona(message)

        # Ensure the conversation exists with the originating gateway/channel
        # before any later reads or writes. Web UI lists are channel-scoped.
        self.memory.ensure_conversation(message.conversation_id, channel=message.channel)

        # 2. History
        history = self.memory.get_history(message.conversation_id, self.max_history)

        # 3. Facts (persona-scoped + global)
        if persona is not None:
            persona_facts = self.memory.get_facts(namespace=persona.namespace)
            global_facts = self.memory.get_facts(namespace="")
            facts = global_facts + persona_facts
        else:
            facts = self.memory.get_facts()

        # 4. Relevant skills
        relevant_skills = self.skills.find_relevant(message.text)
        if not relevant_skills:
            relevant_skills = self.skills.all()

        # 5. Build messages
        messages = self._build_messages(persona, relevant_skills, facts, history, message)

        # 6. Call LLM — with optional tool-use loop
        response_text, tool_trace = await self._run_llm(messages)

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

        # 9. Auto fact extraction (every N turns, when enabled)
        extracted_count = await self._maybe_extract_facts(message, persona)

        # 10. Skill emergence (every N turns, when enabled)
        emerged_skill = await self._maybe_emerge_skill(message)

        # 11. Auto-title the conversation once it has enough content
        new_title = await self._maybe_generate_title(message.conversation_id)

        # Trim old history
        self.memory.trim_history(message.conversation_id, keep=self.max_history * 5)

        return OutboundMessage(
            text=response_text,
            conversation_id=message.conversation_id,
            metadata={
                "skills": [s.name for s in relevant_skills],
                "execution_ids": execution_ids,
                "persona": persona.name if persona else None,
                "facts_extracted": extracted_count,
                "emerged_skill": emerged_skill,
                "new_title": new_title,
                "tool_trace": tool_trace,
            },
        )

    async def _maybe_extract_facts(self, message: InboundMessage, persona: Persona | None) -> int:
        """Extract new facts from history every N turns. Returns count saved."""
        if not self.fact_extractor:
            return 0

        conv_id = message.conversation_id
        self._turn_counts[conv_id] = self._turn_counts.get(conv_id, 0) + 1
        if self._turn_counts[conv_id] % self.fact_extraction_every_n_turns != 0:
            return 0

        namespace = persona.namespace if persona else ""
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

        # Register in emerged_skills table (status=pending_review)
        self.memory.register_emerged_skill(
            name=result.name,
            source_conversation_id=conv_id,
            evaluation_criteria=pattern.rationale,
            status="pending_review",
        )

        # Load into the live registry so it can be used immediately
        try:
            skill = parse_skill(result.path)
            self.skills.register(skill)
        except Exception:
            pass  # Synthesis succeeded but parsing failed — still recorded

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
        persona: Persona | None,
        skills: list,
        facts: list,
        history: list[dict[str, str]],
        message: InboundMessage,
    ) -> list[dict[str, str]]:
        """Build the message list for the LLM call."""
        system_parts = []

        # Persona body comes FIRST — it's the assistant's identity
        if persona is not None and persona.body:
            system_parts.append(f"# Persona: {persona.display_name or persona.name}")
            system_parts.append(persona.body)

        if self.system_prompt:
            system_parts.append(self.system_prompt)

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
            mode = "index" if self.tool_registry and len(self.tool_registry) > 0 else "full"
        if mode == "index":
            return self.skills.as_index_prompt(skills)
        return self.skills.as_full_prompt(skills)
