# Graph Memory

Evidune uses SQLite-backed Cue-Tag-Content graph memory to reconstruct relevant
context before each `serve` turn.

## Behavior

- Graph memory is enabled by default through `memory.graph.enabled`.
- The first backend is the existing `memory.db`; no external graph database,
  embedding store, or new runtime service is required.
- Supported indexed sources are `facts`, `skills`, `messages`, and
  `harness_artifacts`.
- Retrieval is deterministic in v1: cues and tags come from local token and
  metadata extraction, not an additional LLM distillation call.
- The agent searches query cues, traverses Cue-Tag-Content links, records a
  reconstruction trace, and uses selected evidence for prompt context.
- Graph-selected skills are merged with heuristic skill matches and may be
  recorded as skill executions.

## Interfaces

- `MemoryStore` owns graph node, edge, and trace persistence.
- `agent.graph_memory.GraphMemoryService` owns indexing and reconstruction.
- Read-only tools expose search, expansion, content read, and trace explanation.
- `OutboundMessage.metadata.graph_reconstruction` reports the trace id, selected
  node ids, selected skills, evidence count, and traversal actions.

## Constraints

- `memory/` remains storage-only and must not import `agent/` or `skills/`.
- Graph tools are read-only in both plan and execute modes.
- If reconstruction finds no evidence, the agent falls back to legacy fact
  injection and skill behavior.
