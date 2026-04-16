# Product Specs

Product behavior that should survive across refactors belongs here.

Current gaps to fill:

- conversation lifecycle in the web gateway
- identity selection and memory isolation UX

Tracked specs:

- [Conversation Mode](conversation-mode.md): persisted `plan` / `execute` behavior and plan state
- [Swarm Harness](swarm-harness.md): bounded multi-role orchestration, squad persistence,
  task artifacts, and streaming event surfaces
- [Skill Iteration](skill-iteration.md): target self-iteration behavior and the current
  implementation gap across `run` and `serve`
