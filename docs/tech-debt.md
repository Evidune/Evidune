# Tech Debt

Track exceptions that are temporarily allowed so structural checks can prevent
new debt without pretending the existing debt is resolved.

## Oversized Files

- `agent/core.py` (hard limit exception): split prompt assembly and post-response workflows into smaller modules
- `agent/harness/swarm.py` (hard limit exception): split role scheduling, prompt construction, and budget tracking into focused modules
- `agent/iteration_harness.py` (hard limit exception): split packet building, deterministic review policy, and mutation helpers
- `agent/harness/runtime.py` (hard limit exception): split environment lifecycle from observability storage helpers
- `agent/tools/external.py` (hard limit exception): split shell/file/http/python helpers from tool declarations
- `agent/tools/harness_tools.py` (hard limit exception): split runtime, validation, delivery, and maintenance tool declarations
- `core/config.py` (hard limit exception): split config dataclasses from YAML loading
- `core/iteration_helpers.py` (hard limit exception): split outcome iteration orchestration from markdown transform helpers
- `core/loop.py` (hard limit exception): split CLI parsing from runtime wiring
- `gateway/web.py` (hard limit exception): split HTTP handlers from gateway lifecycle
- `memory/schema.py` (hard limit exception): split DDL from migrations and index maintenance helpers
- `memory/store.py` (hard limit exception): split conversation and fact/execution APIs into focused modules
- `web/src/App.svelte` (hard limit exception): split state orchestration from page layout
- `web/src/components/ConversationList.svelte` (hard limit exception): split rendering from sidebar actions

## Cleanup Backlog

- Add browser-validation harness for the web gateway
- Add worktree-local runtime artifacts and validation commands
- Expand product specs for web UX and gateway behavior
