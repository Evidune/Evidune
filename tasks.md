# Evidune Tasks

Canonical task board. Use `[ ]` for pending, `[x]` for completed. Append new tasks at the bottom; archive completed batches to `docs/changelog.md` when the list grows long.

## In Progress

(none)

## Pending

- [ ] Markdown rendering + code highlighting in the chat UI
- [ ] Telegram bot gateway
- [ ] Discord bot gateway
- [ ] GitHub installer + release workflow
- [ ] Comprehensive README with competitive positioning vs Hermes / OpenClaw
- [ ] `docs/architecture.md` deep dive
- [ ] Optional platform workflow skills as separate installable packages
- [ ] Remote CI remediation loop on top of GitHub PR checks
- [ ] Generalized execution-grounded Skill evaluation and automatic iteration (see docs/exec-plans/active/external-outcome-commitments.md)
  - [x] Persist immutable execution/contract lineage and typed evaluation verdicts
  - [x] Link immediate and delayed evidence to exact execution ids and Skill versions
  - [x] Add isolated read-only probes, attribution grades, and version-level aggregation
  - [x] Stage immutable candidates in explicit `evidune eval` runs and add replay, holdout-gated promotion, rejection, and rollback
  - [x] Add pinned `EvaluationCorpus` manifests and a generic `BenchmarkAdapter` contract
  - [x] Add three reviewed official Skills with source-matched faithful fixtures and real-LLM smoke evidence
  - [x] Pin and verify a separate 30-task AppWorld corpus plus a three-task repeated live slice with one development and two source-disjoint holdout tasks; pair sources only where capabilities genuinely match
  - [ ] Complete the full 20-to-30-task repeated AppWorld release run and publish its immutable report bundle
  - [ ] Complete real-corpus and production canary validation; deterministic, replay, and opt-in live-LLM layers are implemented
  - [x] Add known-bad Skill mutation tests and a hidden holdout promotion gate
  - Acceptance: every evaluation promotion or rollback is reproducible from its source executions, evidence, contracts, corpus/model/environment revisions, and validation artifacts; at least one known mutation is detected, repaired into a candidate, and validated with a real LLM on hidden tasks without a hard-gate regression

## Completed

- [x] Initial framework: gateway / agent / skills / memory / core / channels / adapters
- [x] Vite + Svelte web UI with chat interface
- [x] Skill self-iteration wired into the iteration loop (Evidune differentiator)
- [x] Runtime Skill iteration atomically replaces the active version, reloads it immediately, and automatically confirms or rolls it back
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
- [x] Harden self-iteration decision inputs (invalid judge scores, min-sample gates, no zero-evidence rewrites)
- [x] LLM-backed skill rewrite proposals with template fallback and post-rewrite observation window
- [x] Real safety-review checks for iteration harness proposals (honest audit trail)
- [x] Precedence-based signal aggregation with per-execution normalization and recency decay
- [x] End-to-end smoke test with a real Codex LLM and deterministic state evaluator
- [x] One-click Feishu/Lark bot registration with local credential persistence

## Notes

- Don't bundle unrelated tasks in one commit. One task = one or more focused commits.
- Move tasks between sections as status changes.
- For multi-step tasks, indent sub-items under the parent.
