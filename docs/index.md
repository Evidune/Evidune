# Aiflay Docs

This directory is the repository knowledge base and the system of record for
agents working in Aiflay.

## Core Docs

- [Architecture](architecture.md): package boundaries, dependency flow, and file-size policy
- [Quality Score](quality-score.md): current subsystem grades and target improvements
- [Reliability](reliability.md): validation expectations and release gates
- [Tech Debt](tech-debt.md): tracked exceptions and cleanup backlog

## Working Sets

- [Execution Plans](exec-plans/active/README.md): in-flight work that needs durable decision logs
- [Completed Plans](exec-plans/completed/README.md): archived execution records
- [Product Specs](product-specs/index.md): user-facing behavior and UX intent
- [References](references/index.md): stable reference material for humans and agents
- [Generated](generated/README.md): generated or mechanically maintained repo artifacts

## Maintenance Rules

- Add durable product or architecture decisions here rather than burying them in chat.
- Prefer short index documents that point to deeper files over one giant manual.
- Keep links current; `python -m core.docs_lint` enforces the minimum structure.
