"""Built-in tools that give the LLM controlled access to evidune state.

These are safe-by-design (read/write to the local store only) and
always available in tool-use mode. Pair them with external tools
(shell/file/http/etc.) from agent/tools/external.py when configured.
"""

from __future__ import annotations

from agent.tools.base import Tool
from agent.tools.identity_tools import identity_tools as identity_tools
from memory.store import MemoryStore
from skills.registry import SkillRegistry


def memory_tools(memory: MemoryStore, namespace: str = "", allow_write: bool = True) -> list[Tool]:
    """Tools that let the LLM inspect and persist facts."""

    async def get_fact(key: str) -> str:
        v = memory.get_fact(key, namespace=namespace)
        return v if v is not None else f"(no fact named {key!r})"

    async def set_fact(key: str, value: str) -> str:
        memory.set_fact(key, value, source="llm", namespace=namespace)
        return f"Stored {key} = {value!r}"

    async def search_facts(query: str) -> list[dict]:
        results = memory.search_facts(query, namespace=namespace)
        return [{"key": f.key, "value": f.value} for f in results]

    async def list_facts(prefix: str = "") -> list[dict]:
        results = memory.get_facts(prefix=prefix or None, namespace=namespace)
        return [{"key": f.key, "value": f.value} for f in results]

    tools = [
        Tool(
            name="get_fact",
            description="Get the value of a persistent memory fact by key.",
            parameters={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Fact key (e.g. 'user.name')"}
                },
                "required": ["key"],
            },
            handler=get_fact,
        ),
        Tool(
            name="search_facts",
            description="Search persistent memory facts by key or value substring.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            handler=search_facts,
        ),
        Tool(
            name="list_facts",
            description="List facts optionally filtered by key prefix (e.g. 'user.').",
            parameters={
                "type": "object",
                "properties": {
                    "prefix": {
                        "type": "string",
                        "description": "Key prefix filter; empty = all facts",
                    }
                },
            },
            handler=list_facts,
        ),
    ]
    if allow_write:
        tools.insert(
            1,
            Tool(
                name="set_fact",
                description=(
                    "Persist a fact to memory. Use for stable user info, preferences, "
                    "project context — not for transient chat details."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["key", "value"],
                },
                handler=set_fact,
            ),
        )
    return tools


def skill_tools(skills: SkillRegistry) -> list[Tool]:
    """Progressive-disclosure tools for skills.

    The LLM gets an index by default (L0: name + description) and can
    pull the full SKILL.md or a specific reference doc on demand.
    """

    async def list_skills() -> list[dict]:
        return [{"name": s.name, "description": s.description} for s in skills.all()]

    async def get_skill(name: str) -> str:
        skill = skills.get(name)
        if not skill:
            return f"(no skill named {name!r})"
        parts = [f"# {skill.name}\n{skill.description}\n"]
        if skill.triggers:
            parts.append("## Triggers\n" + "\n".join(f"- {t}" for t in skill.triggers))
        if skill.anti_triggers:
            parts.append("## Anti-Triggers\n" + "\n".join(f"- {t}" for t in skill.anti_triggers))
        parts.append(skill.instructions)
        return "\n\n".join(parts)

    async def read_skill_reference(skill_name: str, file: str) -> str:
        content = skills.get_reference(skill_name, file)
        if content is None:
            return f"(no reference {file!r} for skill {skill_name!r})"
        return content

    return [
        Tool(
            name="list_skills",
            description="List all available skills (name + one-line description).",
            parameters={"type": "object", "properties": {}},
            handler=list_skills,
        ),
        Tool(
            name="get_skill",
            description="Load the full instructions for a specific skill.",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            handler=get_skill,
        ),
        Tool(
            name="read_skill_reference",
            description=(
                "Load a specific reference document belonging to a skill "
                "(from its references/ directory)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string"},
                    "file": {"type": "string", "description": "e.g. 'advanced.md'"},
                },
                "required": ["skill_name", "file"],
            },
            handler=read_skill_reference,
        ),
    ]


def conversation_tools(memory: MemoryStore, current_conversation_id: str) -> list[Tool]:
    """Let the LLM browse other conversations (useful for cross-chat continuity)."""

    async def list_conversations(limit: int = 10) -> list[dict]:
        convs = memory.list_conversations(limit=limit)
        return [
            {
                "id": c["id"],
                "title": c["title"] or "(untitled)",
                "preview": c["preview"],
                "updated_at": c["updated_at"],
                "is_current": c["id"] == current_conversation_id,
            }
            for c in convs
        ]

    async def read_conversation(conversation_id: str, limit: int = 20) -> list[dict]:
        return memory.get_history(conversation_id, limit=limit)

    return [
        Tool(
            name="list_conversations",
            description="List recent conversations (id, title, preview).",
            parameters={
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 10}},
            },
            handler=list_conversations,
        ),
        Tool(
            name="read_conversation",
            description="Read the message history of another conversation by id.",
            parameters={
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["conversation_id"],
            },
            handler=read_conversation,
        ),
    ]


def plan_tools(memory: MemoryStore, current_conversation_id: str) -> list[Tool]:
    """Let the LLM maintain a structured plan for the current conversation."""

    async def get_plan() -> dict:
        plan = memory.get_conversation_plan(current_conversation_id)
        if plan is None:
            return {
                "conversation_id": current_conversation_id,
                "explanation": "",
                "items": [],
            }
        return {
            "conversation_id": current_conversation_id,
            "explanation": plan.get("explanation", ""),
            "items": plan.get("items", []),
        }

    async def update_plan(plan: list[dict], explanation: str = "") -> dict:
        memory.update_conversation_plan(
            current_conversation_id,
            items=plan,
            explanation=explanation,
        )
        saved = memory.get_conversation_plan(current_conversation_id) or {
            "explanation": "",
            "items": [],
        }
        return {
            "ok": True,
            "conversation_id": current_conversation_id,
            "explanation": saved["explanation"],
            "items": saved["items"],
        }

    async def clear_plan() -> dict:
        ok = memory.clear_conversation_plan(current_conversation_id)
        return {
            "ok": ok,
            "conversation_id": current_conversation_id,
            "explanation": "",
            "items": [],
        }

    return [
        Tool(
            name="get_plan",
            description="Get the current structured plan for this conversation.",
            parameters={"type": "object", "properties": {}},
            handler=get_plan,
        ),
        Tool(
            name="update_plan",
            description=(
                "Replace the current structured plan for this conversation. "
                "Each item must include a step and a status."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "explanation": {
                        "type": "string",
                        "description": "Optional short summary of the current plan state.",
                    },
                    "plan": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "step": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                },
                            },
                            "required": ["step", "status"],
                        },
                    },
                },
                "required": ["plan"],
            },
            handler=update_plan,
        ),
        Tool(
            name="clear_plan",
            description="Clear the current structured plan for this conversation.",
            parameters={"type": "object", "properties": {}},
            handler=clear_plan,
        ),
    ]
