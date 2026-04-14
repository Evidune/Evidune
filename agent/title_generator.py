"""Auto-generate short titles for conversations.

Called by AgentCore after a conversation accumulates enough content
(default: 3+ messages) and still has no title. The LLM is asked for
a 3-6 word summary that acts as a human-readable handle in the UI.

Cheap by design: single short prompt, no streaming, no schema.
"""

from __future__ import annotations

import re

from agent.llm import LLMClient
from agent.utils import format_conversation

_PROMPT_TEMPLATE = """Give a short, human-readable title for this conversation.

Requirements:
- 3 to 6 words
- no quotes, no period, no emoji
- title case
- describes the topic, not the participants

# Conversation

{conversation}

# Output

Return ONLY the title, nothing else.
"""

_MAX_TITLE_LEN = 80


def _clean_title(raw: str) -> str:
    """Strip code fences, quotes, trailing punctuation; cap length."""
    title = raw.strip()
    if title.startswith("```"):
        title = re.sub(r"^```[a-zA-Z]*\n", "", title)
        title = re.sub(r"\n```\s*$", "", title)
    title = title.strip().strip("\"'`")

    # Take the first non-empty line (some LLMs emit extra commentary)
    for line in title.splitlines():
        line = line.strip().strip("\"'`")
        if line:
            title = line
            break

    # Collapse runs of whitespace inside the chosen line
    title = re.sub(r"\s+", " ", title)
    if title.endswith("."):
        title = title[:-1]
    return title[:_MAX_TITLE_LEN]


class TitleGenerator:
    """Wraps an LLMClient to emit short conversation titles."""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def generate(self, history: list[dict[str, str]]) -> str:
        """Return a short title or empty string if generation fails."""
        if not history:
            return ""
        prompt = _PROMPT_TEMPLATE.format(
            conversation=format_conversation(history, max_content_length=400)
        )
        try:
            raw = await self.llm.complete(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
            )
        except Exception:
            return ""
        return _clean_title(raw)
