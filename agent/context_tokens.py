"""Deterministic token estimates and bounded context helpers."""

from __future__ import annotations

import json
import math
import re
from typing import Any

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_SPACE_RE = re.compile(r"\s+")


def estimate_tokens(text: str) -> int:
    """Cheap estimate that does not undercount Chinese text."""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    return cjk + math.ceil((len(text) - cjk) / 4)


def truncate(text: str, token_budget: int, *, keep_tail: bool = False) -> str:
    if token_budget <= 0:
        return ""
    if estimate_tokens(text) <= token_budget:
        return text
    char_budget = max(16, token_budget * 3)
    if not keep_tail:
        shortened = text[:char_budget]
    else:
        head = int(char_budget * 0.7)
        shortened = f"{text[:head]}\n…\n{text[-(char_budget - head):]}"
    while shortened and estimate_tokens(shortened) > token_budget:
        shortened = shortened[:-32]
    return shortened.rstrip() + "…"


def messages_within_budget(
    records: list[dict[str, Any]], token_budget: int
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used = 0
    for record in reversed(records):
        cost = estimate_tokens(record["content"]) + 6
        if used + cost <= token_budget:
            selected.append(record)
            used += cost
            continue
        if not selected and token_budget > 12:
            truncated = dict(record)
            truncated["content"] = truncate(record["content"], token_budget - 6, keep_tail=True)
            selected.append(truncated)
        break
    return list(reversed(selected))


def prefix_within_budget(records: list[dict[str, Any]], token_budget: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used = 0
    for record in records:
        cost = estimate_tokens(record["content"]) + 8
        if used + cost > token_budget:
            break
        selected.append(record)
        used += cost
        if used >= token_budget:
            break
    return selected


def split_text_within_budget(text: str, token_budget: int) -> list[str]:
    """Split one oversized transcript message without dropping middle content."""
    if not text:
        return [""]
    parts: list[str] = []
    remaining = text
    while remaining:
        if estimate_tokens(remaining) <= token_budget:
            parts.append(remaining)
            break
        low, high = 1, len(remaining)
        while low < high:
            midpoint = (low + high + 1) // 2
            if estimate_tokens(remaining[:midpoint]) <= token_budget:
                low = midpoint
            else:
                high = midpoint - 1
        end = max(1, low)
        parts.append(remaining[:end])
        remaining = remaining[end:]
    return parts


def compact_tool_observation(entry: dict[str, Any], token_budget: int = 400) -> str:
    """Reduce a raw tool trace entry to a bounded durable observation."""
    arguments = json.dumps(entry.get("arguments") or {}, ensure_ascii=False, default=str)
    result = _SPACE_RE.sub(" ", str(entry.get("result") or "")).strip()
    text = f"args={truncate(arguments, 80)} result={result}".strip()
    return truncate(text, token_budget, keep_tail=True)
