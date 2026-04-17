# Architecture

## Goals

- Keep Evidune legible to both humans and coding agents.
- Preserve a small number of explicit package boundaries.
- Encode important constraints mechanically whenever possible.

## Package Boundaries

- `core/`: iteration orchestration, config parsing, and repo-level entrypoints
- `agent/`: prompt assembly, LLM clients, evaluation, synthesis, and tool orchestration
- `gateway/`: bidirectional request/response transports
- `channels/`: outbound reports only
- `identities/`: OpenClaw-style multi-file assistant identity packages
- `skills/`: skill parsing, indexing, and progressive disclosure
- `memory/`: SQLite-backed persistence API
- `adapters/`: generic metrics ingestion adapters used by the iteration loop
- `web/`: frontend for the web gateway

## Dependency Direction

The repository is organized around these practical rules:

- `memory/` must stay storage-focused and must not depend on `agent/`, `gateway/`, `skills/`, `channels/`, `adapters/`, or `identities/`.
- `skills/` must not depend on `agent/`, `gateway/`, `memory/`, `channels/`, `adapters/`, or `identities/`.
- `gateway/` may depend on `agent/` and `gateway/` internals, but should not import `core/`, `skills/`, `memory/`, `channels/`, `adapters/`, or `identities/`.
- `agent/` may depend on `skills/`, `memory/`, `gateway.base`, and `identities/`, but should not import `core/`, `channels/`, or `adapters/`.
- `core/` is the orchestration layer and may wire the system together, but product logic should not migrate into it.

Structural tests enforce the high-signal boundaries above.

## Prompt and Skill Model

- `AGENTS.md` is the entrypoint, not the encyclopedia.
- `docs/` is the durable source of truth.
- Skills should use progressive disclosure by default: expose a compact index in the
  system prompt and load full `SKILL.md` or `references/` only when needed.
- Platform-specific operating knowledge and workflows belong in `skills/`, not in
  hardcoded system behavior. Keep built-in adapters generic and reusable.

## File Size Policy

- Python files should usually stay below 300 lines.
- Svelte and TypeScript files should usually stay below 250 lines.
- Existing exceptions must be tracked in [tech-debt.md](tech-debt.md) until split.
