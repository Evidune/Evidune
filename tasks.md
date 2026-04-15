# Aiflay Tasks

Canonical task board. Use `[ ]` for pending, `[x]` for completed. Append new tasks at the bottom; archive completed batches to `docs/changelog.md` when the list grows long.

## In Progress

(none)

## Pending

- [ ] Add `aiflay init` CLI command to scaffold starter `aiflay.yaml`
- [ ] Browser-driven validation harness for the web gateway
- [ ] Worktree-local runtime artifacts and validation commands
- [ ] Agent-visible logs, metrics, and traces for local runs
- [ ] Streaming responses for the web gateway (SSE)
- [ ] Markdown rendering + code highlighting in the chat UI
- [ ] End-to-end smoke test with a real LLM (mocked or via OpenRouter free tier)
- [ ] Telegram bot gateway
- [ ] Discord bot gateway
- [ ] PyPI publishing setup (release workflow)
- [ ] Comprehensive README with competitive positioning vs Hermes / OpenClaw
- [ ] `docs/architecture.md` deep dive
- [ ] Adapter for Zhihu Creator Center API
- [ ] Adapter for Xiaohongshu metrics

## Completed

- [x] Initial framework: gateway / agent / skills / memory / core / channels / adapters
- [x] Vite + Svelte web UI with chat interface
- [x] Skill self-iteration wired into the iteration loop (Aiflay differentiator)
- [x] Pre-commit hooks + commitlint + AGENTS.md collaboration baseline
- [x] Docs knowledge base skeleton with architecture, quality, reliability, and tech debt records
- [x] Repo docs lint, CI workflow, and structural guardrails
- [x] Progressive skill disclosure default with compatibility mode
- [x] Replace monolithic personas with OpenClaw-style identity packages

## Notes

- Don't bundle unrelated tasks in one commit. One task = one or more focused commits.
- Move tasks between sections as status changes.
- For multi-step tasks, indent sub-items under the parent.
