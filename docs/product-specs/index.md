# Product Specs

Product behavior that should survive across refactors belongs here.

Current gaps to fill:

- conversation lifecycle in the web gateway
- identity selection and memory isolation UX

Tracked specs:

- [Conversation Mode](conversation-mode.md): persisted `plan` / `execute` behavior and plan state
- [Outcome Governance](outcome-governance.md): split execution-quality evidence from business KPI governance and define the normalized outcome loop
- [Swarm Harness](swarm-harness.md): bounded multi-role orchestration, squad persistence,
  task artifacts, and streaming event surfaces
- [Skill Iteration](skill-iteration.md): shared governance workflow, lifecycle semantics,
  and restart-safe runtime behavior across `run` and `serve`
- [Self-Management Tools](self-management-tools.md): structured config
  inspection, validated config patching, and restart request behavior
