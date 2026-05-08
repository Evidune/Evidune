"""Detect whether a user turn is asking for a skill transaction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.llm import LLMClient
from agent.utils import format_conversation, parse_json_response


@dataclass
class SkillIntent:
    is_skill_request: bool
    intent: str = "none"
    confidence: float = 0.0
    reason: str = ""


_VALID_INTENTS = {"create", "update", "reuse", "debug", "none"}

_PROMPT_TEMPLATE = """You decide whether the current user message is asking for a skill transaction.

A skill transaction means creating, updating, reusing, or debugging a reusable
Claude/OpenClaw-style skill/capability/workflow package. It does not mean merely
answering a question, listing options, or doing a one-off task.

Treat these Chinese phrases as skill-transaction signals when they refer to a
reusable capability:
- 创造相关 skill
- 做成能力
- 封装工作流
- 沉淀为可复用流程
- 创建/建立/生成/实现/提炼 skill

Only mark a message as a skill transaction when the user is asking to package,
change, reuse, or inspect a durable skill/workflow. Do not infer a skill
transaction from messages that only ask the assistant to answer, research,
execute, connect, run, use available tools, or continue the immediate task
unless the message also asks for reusable packaging or skill lifecycle work.

# Recent conversation

{history}

# Current user message

{message}

# Output

Return ONLY JSON:
{{
  "is_skill_request": true,
  "intent": "create",
  "confidence": 0.9,
  "reason": "The user explicitly asks to create a reusable skill."
}}

Use intent one of: create, update, reuse, debug, none.
If uncertain or not a skill transaction, set is_skill_request=false, intent="none",
and confidence below 0.6.
"""


def _parse_skill_intent(raw: str) -> SkillIntent:
    data = parse_json_response(raw)
    if not isinstance(data, dict):
        return SkillIntent(False, confidence=0.0, reason="unparseable_response")

    intent = str(data.get("intent", "none")).strip().lower()
    if intent not in _VALID_INTENTS:
        intent = "none"
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    is_skill_request = bool(data.get("is_skill_request", False)) and intent != "none"
    reason = str(data.get("reason", "")).strip()
    return SkillIntent(is_skill_request, intent=intent, confidence=confidence, reason=reason)


class SkillIntentDetector:
    """LLM-backed detector for explicit skill creation/update/debug intent."""

    def __init__(self, judge: LLMClient) -> None:
        self.judge = judge

    async def detect(
        self,
        message: str,
        history: list[dict[str, str]] | None = None,
        **llm_kwargs: Any,
    ) -> SkillIntent:
        prompt = _PROMPT_TEMPLATE.format(
            history=format_conversation((history or [])[-6:], max_content_length=500),
            message=message,
        )
        kwargs = {"temperature": 0.0, **llm_kwargs}
        raw = await self.judge.complete([{"role": "user", "content": prompt}], **kwargs)
        return _parse_skill_intent(raw)
