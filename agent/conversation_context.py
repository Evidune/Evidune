"""Token-budgeted conversation context, summaries, and tool observations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.context_tokens import (
    compact_tool_observation,
    estimate_tokens,
    messages_within_budget,
    prefix_within_budget,
    split_text_within_budget,
    truncate,
)
from agent.llm import LLMClient
from memory.store import MemoryStore


@dataclass
class ConversationContext:
    messages: list[dict[str, str]] = field(default_factory=list)
    summary: str = ""
    tool_observations: list[dict[str, Any]] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)


class ConversationContextManager:
    """Assemble bounded prompt context while preserving the full transcript."""

    def __init__(
        self,
        memory: MemoryStore,
        llm: LLMClient,
        *,
        recent_token_budget: int = 20_000,
        summary_token_budget: int = 3_000,
        tool_observation_token_budget: int = 2_000,
    ) -> None:
        self.memory = memory
        self.llm = llm
        self.recent_token_budget = recent_token_budget
        self.summary_token_budget = summary_token_budget
        self.tool_observation_token_budget = tool_observation_token_budget

    async def build(self, conversation_id: str) -> ConversationContext:
        records = self.memory.get_history_records(conversation_id, limit=None)
        recent = messages_within_budget(records, self.recent_token_budget)
        first_recent_id = recent[0]["id"] if recent else (records[-1]["id"] + 1 if records else 1)
        await self._summarize_before(conversation_id, records, first_recent_id)
        summary_row = self.memory.get_conversation_summary(conversation_id) or {}
        observations = self._recent_tool_observations(conversation_id)
        summary = truncate(str(summary_row.get("summary") or ""), self.summary_token_budget)
        recent_tokens = sum(estimate_tokens(item["content"]) + 6 for item in recent)
        observation_tokens = sum(estimate_tokens(item["summary"]) + 6 for item in observations)
        report = {
            "budgets": {
                "recent_messages": self.recent_token_budget,
                "conversation_summary": self.summary_token_budget,
                "tool_observations": self.tool_observation_token_budget,
            },
            "transcript": {
                "message_count": len(records),
                "estimated_tokens": sum(estimate_tokens(item["content"]) + 6 for item in records),
                "preserved_in_full": True,
                "oldest_message_id": records[0]["id"] if records else None,
                "newest_message_id": records[-1]["id"] if records else None,
            },
            "summary": {
                "present": bool(summary),
                "estimated_tokens": estimate_tokens(summary),
                "covered_through_message_id": int(
                    summary_row.get("covered_through_message_id") or 0
                ),
                "source_message_count": int(summary_row.get("source_message_count") or 0),
            },
            "recent_messages": {
                "count": len(recent),
                "estimated_tokens": recent_tokens,
                "first_message_id": recent[0]["id"] if recent else None,
                "last_message_id": recent[-1]["id"] if recent else None,
            },
            "tool_observations": {
                "count": len(observations),
                "estimated_tokens": observation_tokens,
            },
        }
        return ConversationContext(
            messages=[{"role": item["role"], "content": item["content"]} for item in recent],
            summary=summary,
            tool_observations=observations,
            report=report,
        )

    def recent_messages(self, conversation_id: str) -> list[dict[str, str]]:
        records = self.memory.get_history_records(conversation_id, limit=None)
        return [
            {"role": item["role"], "content": item["content"]}
            for item in messages_within_budget(records, self.recent_token_budget)
        ]

    async def _summarize_before(
        self,
        conversation_id: str,
        records: list[dict[str, Any]],
        first_recent_id: int,
    ) -> None:
        current = self.memory.get_conversation_summary(conversation_id) or {}
        covered = int(current.get("covered_through_message_id") or 0)
        pending = [item for item in records if covered < item["id"] < first_recent_id]
        source_count = int(current.get("source_message_count") or 0)
        summary = str(current.get("summary") or "")
        while pending:
            first = pending[0]
            if estimate_tokens(first["content"]) + 8 > self.recent_token_budget:
                updated_summary = summary
                try:
                    for content_part in split_text_within_budget(
                        first["content"],
                        max(1, self.recent_token_budget - 8),
                    ):
                        chunk_record = dict(first)
                        chunk_record["content"] = content_part
                        updated_summary = await self._summarize(
                            updated_summary,
                            [chunk_record],
                        )
                        if not updated_summary:
                            return
                except Exception:
                    return
                summary = updated_summary
                covered = first["id"]
                source_count += 1
                self.memory.save_conversation_summary(
                    conversation_id,
                    summary,
                    covered_through_message_id=covered,
                    source_message_count=source_count,
                    estimated_tokens=estimate_tokens(summary),
                )
                pending = pending[1:]
                continue
            chunk = prefix_within_budget(pending, self.recent_token_budget)
            if not chunk:
                return
            try:
                updated = await self._summarize(summary, chunk)
            except Exception:
                return
            if not updated:
                return
            summary = updated
            covered = chunk[-1]["id"]
            source_count += len(chunk)
            self.memory.save_conversation_summary(
                conversation_id,
                summary,
                covered_through_message_id=covered,
                source_message_count=source_count,
                estimated_tokens=estimate_tokens(summary),
            )
            pending = [item for item in pending if item["id"] > covered]

    async def _summarize(self, existing_summary: str, records: list[dict[str, Any]]) -> str:
        transcript = "\n".join(
            f"[message_id={item['id']} role={item['role']}] {item['content']}" for item in records
        )
        prompt = (
            "Update a durable conversation summary for future turns. Preserve concrete "
            "goals, decisions, constraints, identifiers, unfinished work, errors, and "
            "user preferences. Remove repetition and transient chatter. Do not invent facts.\n\n"
            f"# Existing summary\n{existing_summary or '(none)'}\n\n"
            f"# New transcript segment\n{transcript}\n\n"
            f"Return only the updated summary, within about {self.summary_token_budget} tokens."
        )
        result = await self.llm.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=self.summary_token_budget,
        )
        return truncate(result.strip(), self.summary_token_budget)

    def _recent_tool_observations(self, conversation_id: str) -> list[dict[str, Any]]:
        if self.tool_observation_token_budget <= 0:
            return []
        observations = self.memory.list_tool_observations(conversation_id, limit=None)
        selected: list[dict[str, Any]] = []
        used = 0
        for item in reversed(observations):
            cost = estimate_tokens(item["summary"]) + 6
            if used + cost > self.tool_observation_token_budget:
                if not selected and self.tool_observation_token_budget > 6:
                    truncated = dict(item)
                    truncated["summary"] = truncate(
                        item["summary"],
                        self.tool_observation_token_budget - 6,
                        keep_tail=True,
                    )
                    selected.append(truncated)
                break
            selected.append(item)
            used += cost
            if used >= self.tool_observation_token_budget:
                break
        return list(reversed(selected))

    def persist_tool_trace(
        self,
        conversation_id: str,
        turn_message_id: int,
        tool_trace: list[dict[str, Any]],
    ) -> int:
        saved = 0
        for entry in tool_trace:
            name = str(entry.get("name") or "unknown")
            summary = compact_tool_observation(entry)
            if not summary:
                continue
            self.memory.add_tool_observation(
                conversation_id,
                turn_message_id=turn_message_id,
                tool_name=name,
                summary=summary,
                is_error=bool(entry.get("is_error")),
            )
            saved += 1
        return saved
