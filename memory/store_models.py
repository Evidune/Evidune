"""Data models used across the memory package.

Split out of `store.py` so `rows.py` can import `Fact` without pulling
in the full MemoryStore class (which in turn pulls sqlite3 / pathlib).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Message:
    role: str  # "user" | "assistant" | "system"
    content: str
    conversation_id: str
    created_at: str = ""


@dataclass
class Fact:
    key: str
    value: str
    source: str = "agent"
    created_at: str = ""
    updated_at: str = ""
