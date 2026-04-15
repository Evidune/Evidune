# Tech Debt

Track exceptions that are temporarily allowed so structural checks can prevent
new debt without pretending the existing debt is resolved.

## Oversized Files

- `agent/core.py` (hard limit exception): split prompt assembly and post-response workflows into smaller modules
- `agent/tools/external.py` (hard limit exception): split shell/file/http/python helpers from tool declarations
- `core/config.py` (hard limit exception): split config dataclasses from YAML loading
- `core/loop.py` (hard limit exception): split CLI parsing from runtime wiring
- `gateway/web.py` (hard limit exception): split HTTP handlers from gateway lifecycle
- `memory/store.py` (hard limit exception): split conversation and fact/execution APIs into focused modules
- `web/src/App.svelte` (hard limit exception): split state orchestration from page layout
- `web/src/components/ConversationList.svelte` (hard limit exception): split rendering from sidebar actions

## Cleanup Backlog

- Add browser-validation harness for the web gateway
- Add worktree-local runtime artifacts and validation commands
- Expand product specs for web UX and gateway behavior
