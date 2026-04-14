"""Row → object conversion helpers for the memory store.

Separated so the store's query methods stay thin and the conversions
live in one place (previously each method built the Fact/dict inline,
duplicating the shape across get/search operations).
"""

from __future__ import annotations

import json
from typing import Any

from memory.store_models import Fact


def row_to_fact(row) -> Fact:
    return Fact(
        key=row["key"],
        value=row["value"],
        source=row["source"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_execution(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "skill_name": row["skill_name"],
        "conversation_id": row["conversation_id"],
        "user_input": row["user_input"],
        "assistant_output": row["assistant_output"],
        "signals": json.loads(row["signals_json"] or "{}"),
        "score": row["cross_model_score"],
        "evaluator_reasoning": row["evaluator_reasoning"],
        "created_at": row["created_at"],
    }
