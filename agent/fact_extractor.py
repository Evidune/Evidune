"""Auto fact extraction from conversation history.

Periodically asks an LLM to scan recent messages and identify
persistent facts worth remembering for future sessions. Existing
facts are passed in so the model can avoid duplicates.

A "fact" is something stable about the user, their preferences,
or their context — NOT a one-off question, transient state, or
the assistant's own reasoning.

Usage:
    extractor = FactExtractor(judge=some_llm_client)
    candidates = await extractor.extract(history, existing_facts=[...])
    for c in candidates:
        if c.confidence >= 0.7:
            memory.set_fact(c.key, c.value, source="auto", namespace=ns)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.llm import LLMClient
from agent.utils import format_conversation, format_facts_inline, parse_json_response
from memory.store import Fact


@dataclass
class ExtractedFact:
    key: str
    value: str
    confidence: float  # 0.0 - 1.0


_PROMPT_TEMPLATE = """You are a memory extractor for a personal AI assistant.

Look at the recent conversation and identify persistent facts worth remembering for future sessions.

A fact must be:
- Stable (true beyond this conversation)
- About the user, their preferences, projects, or stable context
- Not already in the existing memory

Skip:
- Transient questions ("what time is it")
- The assistant's own reasoning
- Speculation
- Anything you're less than 60% sure about

# Existing memory (DO NOT re-extract these)

{existing_block}

# Recent conversation

{conversation_block}

# Output

Return ONLY a JSON object in this exact format. If no new facts, return {{"facts": []}}.

{{
  "facts": [
    {{"key": "user.name", "value": "Alice", "confidence": 0.95}},
    {{"key": "project.tech_stack", "value": "Python + FastAPI", "confidence": 0.85}}
  ]
}}

Use snake_case dotted keys (user.X, project.Y, preference.Z).
"""


# Backwards-compatible alias for tests
_format_existing = format_facts_inline
_format_conversation = format_conversation


def _parse_response(raw: str) -> list[ExtractedFact]:
    """Parse JSON {facts: [...]} from the LLM response. Tolerant of fences."""
    data = parse_json_response(raw)
    if data is None:
        return []

    items = data.get("facts", [])
    if not isinstance(items, list):
        return []

    out: list[ExtractedFact] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip()
        value = str(item.get("value", "")).strip()
        if not key or not value:
            continue
        try:
            conf = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        out.append(ExtractedFact(key=key, value=value, confidence=conf))
    return out


class FactExtractor:
    """Extract persistent facts from conversation history using an LLM."""

    def __init__(self, judge: LLMClient) -> None:
        self.judge = judge

    async def extract(
        self,
        history: list[dict[str, str]],
        existing_facts: list[Fact] | None = None,
        **llm_kwargs: Any,
    ) -> list[ExtractedFact]:
        """Run extraction. Returns candidate facts (caller filters by confidence)."""
        if not history:
            return []

        prompt = _PROMPT_TEMPLATE.format(
            existing_block=_format_existing(existing_facts or []),
            conversation_block=_format_conversation(history),
        )
        kwargs = {"temperature": 0.1, **llm_kwargs}
        raw = await self.judge.complete(
            [{"role": "user", "content": prompt}],
            **kwargs,
        )
        return _parse_response(raw)
