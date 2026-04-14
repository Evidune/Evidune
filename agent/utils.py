"""Shared helpers for the LLM-facing agent modules.

Before this file existed, the same code-fence stripping, JSON-with-
fallback parsing, and conversation-history formatting logic was copy-
pasted across self_evaluator, fact_extractor, pattern_detector, and
skill_synthesizer. Consolidating it here keeps those modules focused
on their domain prompts and output schemas.
"""

from __future__ import annotations

import json
import re
from typing import Any

_CODE_FENCE_OPEN = re.compile(r"^```[a-zA-Z]*\n")
_CODE_FENCE_CLOSE = re.compile(r"\n```\s*$")
_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def strip_code_fence(raw: str) -> str:
    """Remove surrounding ```lang ... ``` fences if present."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = _CODE_FENCE_OPEN.sub("", cleaned, count=1)
        cleaned = _CODE_FENCE_CLOSE.sub("", cleaned)
    return cleaned


def parse_json_response(
    raw: str,
    hint_pattern: re.Pattern[str] | None = None,
) -> dict[str, Any] | None:
    """Parse JSON from an LLM response, tolerating surrounding text.

    Strategy:
    1. Strip code fences and try json.loads on the whole thing.
    2. If that fails, run `hint_pattern` (or a generic JSON-object regex)
       to find the first JSON-looking blob and try again.
    3. Returns the parsed dict, or None if nothing parseable was found.
    """
    cleaned = strip_code_fence(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        pattern = hint_pattern or _JSON_OBJECT_RE
        m = pattern.search(cleaned)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def format_conversation(
    history: list[dict[str, str]],
    max_content_length: int = 600,
) -> str:
    """Render a conversation history as a plain-text block for prompts.

    Each message becomes `[role] content`. Content longer than
    `max_content_length` characters is truncated with a trailing ellipsis
    so the prompt stays bounded.
    """
    if not history:
        return "(empty)"

    lines: list[str] = []
    for msg in history:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if len(content) > max_content_length:
            content = content[:max_content_length] + "…"
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)


def format_facts_inline(facts: list) -> str:
    """Render a list of Fact objects as bullet lines 'key: value'."""
    if not facts:
        return "(none)"
    return "\n".join(f"- {f.key}: {f.value}" for f in facts)


def format_skill_names(names: list[str]) -> str:
    """Render a list of skill names as bullet lines."""
    if not names:
        return "(none)"
    return "\n".join(f"- {n}" for n in names)
