"""Small multilingual lexical helpers shared by SQLite memory search."""

from __future__ import annotations

import re

_PART_RE = re.compile(r"[a-z0-9_]+|[\u3400-\u9fff]+", re.IGNORECASE)
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "for",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "should",
    "the",
    "this",
    "to",
    "use",
    "with",
    "work",
}


def lexical_terms(text: str, *, limit: int = 80) -> list[str]:
    """Return stable Latin tokens and overlapping CJK bigrams for FTS5."""
    terms: list[str] = []
    for part in _PART_RE.findall(text or ""):
        value = part.lower()
        if "\u3400" <= value[0] <= "\u9fff":
            candidates = (
                [value]
                if len(value) <= 2
                else [value[index : index + 2] for index in range(len(value) - 1)]
            )
        else:
            candidates = [value] if len(value) >= 2 and value not in _STOPWORDS else []
        for candidate in candidates:
            if candidate not in terms:
                terms.append(candidate)
            if len(terms) >= limit:
                return terms
    return terms


def fts_text(*parts: str) -> str:
    """Render text into the whitespace-separated form indexed by FTS5."""
    return " ".join(lexical_terms(" ".join(parts), limit=400))
