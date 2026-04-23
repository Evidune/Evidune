# Evidune Tasks

Canonical task board. Use `[ ]` for pending, `[x]` for completed. Append new tasks at the bottom; archive completed batches to `docs/changelog.md` when the list grows long.

## In Progress

(none)

## Pending

- [ ] Markdown rendering + code highlighting in the chat UI
- [ ] End-to-end smoke test with a real LLM (mocked or via OpenRouter free tier)
- [ ] Telegram bot gateway
- [ ] Discord bot gateway
- [ ] GitHub installer + release workflow
- [ ] Comprehensive README with competitive positioning vs Hermes / OpenClaw
- [ ] `docs/architecture.md` deep dive
- [ ] Optional platform workflow skills as separate installable packages
- [ ] Remote CI remediation loop on top of GitHub PR checks

## Completed

- [x] Initial framework: gateway / agent / skills / memory / core / channels / adapters
- [x] Vite + Svelte web UI with chat interface
- [x] Skill self-iteration wired into the iteration loop (Evidune differentiator)
- [x] Pre-commit hooks + commitlint + AGENTS.md collaboration baseline
- [x] Docs knowledge base skeleton with architecture, quality, reliability, and tech debt records
- [x] Repo docs lint, CI workflow, and structural guardrails
- [x] Progressive skill disclosure default with compatibility mode
- [x] Replace monolithic personas with OpenClaw-style identity packages
- [x] Add conversation-scoped plan tools with persistent state
- [x] Add persisted `plan` / `execute` conversation modes
- [x] Surface conversation mode and plan state in the web UI
- [x] Persist iteration run ledger and CLI inspection commands
- [x] Add `evidune init` CLI command to scaffold starter `evidune.yaml`
- [x] Add worktree-local runtime artifacts and runnable local examples
- [x] Streaming responses for the web gateway (SSE)
- [x] Auto-activate emerged skills and reload them across restarts
- [x] Connect feedback signals and evaluator scores into a single skill-iteration decision loop
- [x] Add audit trail, disable, and rollback flow for automatic skill activation
- [x] Extend outcome-driven iteration from evidence-only updates to direct `SKILL.md` rewrites
- [x] Add evidence-backed rewrite guardrails to prevent automatic skill drift
- [x] Add end-to-end coverage for skill generation, activation, reload, rewrite, and rollback
- [x] Browser-driven validation harness for the web gateway
- [x] Task-scoped harness runtime environments with structured logs / metrics / traces
- [x] Playwright-backed validation tools and validation artifacts in swarm tasks
- [x] Local-first delivery pipeline with branch / commit / optional GitHub PR flow
- [x] Structured maintenance sweep with targeted follow-up tasks
- [x] Richer web timeline UI for environment / validation / delivery summaries

## Notes

- Don't bundle unrelated tasks in one commit. One task = one or more focused commits.
- Move tasks between sections as status changes.
- For multi-step tasks, indent sub-items under the parent.
