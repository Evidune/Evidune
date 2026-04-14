"""Agent core — orchestrates skills, memory, persona, and LLM."""

from __future__ import annotations

from agent.llm import LLMClient
from gateway.base import InboundMessage, OutboundMessage
from memory.store import MemoryStore
from personas.loader import Persona
from personas.registry import PersonaRegistry
from skills.registry import SkillRegistry


class AgentCore:
    """Central agent that handles messages using skills + memory + persona + LLM."""

    def __init__(
        self,
        llm: LLMClient,
        skill_registry: SkillRegistry,
        memory: MemoryStore,
        system_prompt: str = "",
        max_history: int = 20,
        persona_registry: PersonaRegistry | None = None,
    ) -> None:
        self.llm = llm
        self.skills = skill_registry
        self.memory = memory
        self.system_prompt = system_prompt
        self.max_history = max_history
        self.personas = persona_registry or PersonaRegistry()

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

        # 6. Call LLM
        response_text = await self.llm.complete(messages)

        # 7. Store in memory
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

        # Trim old history
        self.memory.trim_history(message.conversation_id, keep=self.max_history * 5)

        return OutboundMessage(
            text=response_text,
            conversation_id=message.conversation_id,
            metadata={
                "skills": [s.name for s in relevant_skills],
                "execution_ids": execution_ids,
                "persona": persona.name if persona else None,
            },
        )

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
        skill_prompt = self.skills.as_system_prompt(skills)
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
