"""Agent core — orchestrates skills, memory, and LLM."""

from __future__ import annotations

from agent.llm import LLMClient
from gateway.base import InboundMessage, OutboundMessage
from memory.store import MemoryStore
from skills.registry import SkillRegistry


class AgentCore:
    """Central agent that handles messages using skills + memory + LLM."""

    def __init__(
        self,
        llm: LLMClient,
        skill_registry: SkillRegistry,
        memory: MemoryStore,
        system_prompt: str = "",
        max_history: int = 20,
    ) -> None:
        self.llm = llm
        self.skills = skill_registry
        self.memory = memory
        self.system_prompt = system_prompt
        self.max_history = max_history

    async def handle(self, message: InboundMessage) -> OutboundMessage:
        """Process an inbound message and return a response.

        Flow:
        1. Load conversation history from memory
        2. Load relevant facts
        3. Find relevant skills
        4. Build prompt (system + skills + facts + history + message)
        5. Call LLM
        6. Store message + response in memory
        """
        # 1. History
        history = self.memory.get_history(message.conversation_id, self.max_history)

        # 2. Facts
        facts = self.memory.get_facts()

        # 3. Relevant skills
        relevant_skills = self.skills.find_relevant(message.text)
        # Fallback: if no relevant skills found, use all skills
        if not relevant_skills:
            relevant_skills = self.skills.all()

        # 4. Build messages
        messages = self._build_messages(relevant_skills, facts, history, message)

        # 5. Call LLM
        response_text = await self.llm.complete(messages)

        # 6. Store in memory
        self.memory.add_message(message.conversation_id, "user", message.text)
        self.memory.add_message(message.conversation_id, "assistant", response_text)

        # 7. Record skill execution(s) for outcome iteration / feedback
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
            },
        )

    def _build_messages(
        self,
        skills: list,
        facts: list,
        history: list[dict[str, str]],
        message: InboundMessage,
    ) -> list[dict[str, str]]:
        """Build the message list for the LLM call."""
        # System prompt
        system_parts = []

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
