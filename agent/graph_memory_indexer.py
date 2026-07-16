"""Source indexing for SQLite Cue-Tag-Content graph memory."""

from __future__ import annotations

from typing import Any

from agent.graph_memory_models import extract_cues
from memory.store import Fact, MemoryStore
from skills.loader import Skill


def sync_graph_sources(
    memory: MemoryStore,
    index_sources: list[str],
    *,
    conversation_id: str,
    facts: list[Fact],
    history: list[dict[str, str]],
    skills: list[Skill],
) -> None:
    if "facts" in index_sources:
        for fact in facts:
            _index_content(
                memory,
                key=fact.key,
                text=f"{fact.key}: {fact.value}",
                source_type="fact",
                source_id=fact.key,
                tags=["fact", fact.source or "memory"],
                metadata={"fact_key": fact.key},
            )
    if "skills" in index_sources:
        for skill in skills:
            _index_skill(memory, skill)
    if "messages" in index_sources:
        _index_messages(memory, conversation_id, history)
    if "harness_artifacts" in index_sources and conversation_id:
        for task in memory.list_harness_tasks(conversation_id=conversation_id, limit=5):
            for artifact in memory.list_harness_artifacts(task["id"]):
                text = f"{artifact.get('summary', '')}\n{artifact.get('content', '')}".strip()
                if not text:
                    continue
                _index_content(
                    memory,
                    key=f"{task['id']}:{artifact['id']}",
                    text=text,
                    source_type="harness_artifact",
                    source_id=f"{task['id']}:{artifact['id']}",
                    tags=["harness_artifact", artifact.get("kind", "")],
                    metadata={
                        "task_id": task["id"],
                        "artifact_id": artifact["id"],
                        "kind": artifact.get("kind", ""),
                    },
                )


def _index_messages(
    memory: MemoryStore,
    conversation_id: str,
    history: list[dict[str, str]],
) -> None:
    # ponytail: rescan the local transcript for now; add an indexing watermark
    # if conversation histories become large enough to matter.
    messages = (
        memory.get_history_records(conversation_id, limit=None) if conversation_id else history
    )
    observations = (
        memory.list_tool_observations(conversation_id, limit=None) if conversation_id else []
    )
    if conversation_id:
        valid_source_ids = {
            f"{conversation_id}:message:{message['id']}"
            for message in messages
            if message.get("id") is not None
        }
        valid_source_ids.update(
            f"{conversation_id}:tool:{observation['id']}" for observation in observations
        )
        memory.prune_graph_memory_message_sources(conversation_id, valid_source_ids)
    for index, message in enumerate(messages):
        content = message.get("content", "")
        if not content:
            continue
        message_id = message.get("id")
        stable_id = str(message_id) if message_id is not None else f"legacy:{index}"
        _index_content(
            memory,
            key=f"{conversation_id}:message:{stable_id}:{message.get('role', '')}",
            text=content,
            source_type="message",
            source_id=f"{conversation_id}:message:{stable_id}",
            tags=["message", message.get("role", "")],
            metadata={
                "conversation_id": conversation_id,
                "message_id": message_id,
                "role": message.get("role", ""),
            },
        )
    for observation in observations:
        observation_id = observation["id"]
        _index_content(
            memory,
            key=f"{conversation_id}:tool:{observation_id}:{observation['tool_name']}",
            text=observation["summary"],
            source_type="message",
            source_id=f"{conversation_id}:tool:{observation_id}",
            tags=["tool_observation", observation["tool_name"]],
            metadata={
                "conversation_id": conversation_id,
                "tool_observation_id": observation_id,
                "tool_name": observation["tool_name"],
                "is_error": observation["is_error"],
            },
        )


def _index_skill(memory: MemoryStore, skill: Skill) -> None:
    text = " ".join(
        part
        for part in [
            skill.name,
            skill.description,
            " ".join(skill.triggers),
            " ".join(skill.tags),
            skill.instructions,
        ]
        if part
    )
    _index_content(
        memory,
        key=skill.name,
        text=text,
        source_type="skill",
        source_id=skill.name,
        tags=["skill", *skill.tags],
        metadata={"skill_name": skill.name},
    )
    for ref_name, ref_text in skill.references.items():
        _index_content(
            memory,
            key=f"{skill.name}:{ref_name}",
            text=f"{ref_name}\n{ref_text[:1200]}",
            source_type="skill_reference",
            source_id=f"{skill.name}:{ref_name}",
            tags=["skill_reference", skill.name],
            metadata={"skill_name": skill.name, "reference": ref_name},
        )


def _index_content(
    memory: MemoryStore,
    *,
    key: str,
    text: str,
    source_type: str,
    source_id: str,
    tags: list[str],
    metadata: dict[str, Any],
) -> str:
    content_id = memory.upsert_graph_memory_node(
        node_type="content",
        key=key,
        text=text,
        source_type=source_type,
        source_id=source_id,
        metadata=metadata,
    )
    cue_ids = [
        memory.upsert_graph_memory_node(
            node_type="cue",
            key=cue,
            text=cue,
            source_type=source_type,
            source_id=source_id,
        )
        for cue in extract_cues(f"{key} {text}")
    ]
    tag_ids = [
        memory.upsert_graph_memory_node(
            node_type="tag",
            key=tag,
            text=tag,
            source_type=source_type,
            source_id=source_id,
        )
        for tag in _normalise_tags(tags)
    ]
    for cue_id in cue_ids:
        for tag_id in tag_ids:
            memory.upsert_graph_memory_edge(cue_id, tag_id, "cue_tag", weight=0.8)
        memory.upsert_graph_memory_edge(content_id, cue_id, "content_cue", weight=0.4)
    for tag_id in tag_ids:
        memory.upsert_graph_memory_edge(tag_id, content_id, "tag_content", weight=1.0)
        memory.upsert_graph_memory_edge(content_id, tag_id, "content_tag", weight=0.4)
    return content_id


def _normalise_tags(tags: list[str]) -> list[str]:
    result: list[str] = []
    for tag in tags:
        for cue in extract_cues(tag, max_cues=4):
            if cue not in result:
                result.append(cue)
    return result or ["memory"]
