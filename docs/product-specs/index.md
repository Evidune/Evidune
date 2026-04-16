# Product Specs

Product behavior that should survive across refactors belongs here.

Current gaps to fill:

- conversation lifecycle in the web gateway
- skill iteration decision semantics for feedback signals and evaluator scores
- emerged skill activation, restart reload, and rollback lifecycle
- automatic skill rewrite boundaries and drift guardrails
- identity selection and memory isolation UX

Tracked specs:

- [Conversation Mode](conversation-mode.md): persisted `plan` / `execute` behavior and plan state
- [Skill Iteration](skill-iteration.md): target self-iteration behavior and the current
  implementation gap across `run` and `serve`
