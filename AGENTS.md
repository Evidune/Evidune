# Agents Guidance for Evidune

This file is the agent entrypoint for this repository. Treat it as a map, not
the full manual. Repository-local docs under `docs/` are the system of record.

## Precedence

Follow instructions in this order:

1. System / developer messages
2. User request
3. This file
4. Linked repository docs

`CLAUDE.md`, `GEMINI.md`, and `.cursorrules` must stay symlinked to this file.

## Mission

Evidune is a lightweight AI agent framework with two operating modes:

- `evidune run`: metrics -> analysis -> skill/reference updates -> optional git commit
- `evidune serve`: interactive agent with gateways, memory, identities, skills, and tools

The product differentiator is outcome-driven skill self-iteration. Preserve that
behavior carefully.

## Repo Map

- `docs/index.md`: documentation hub
- `docs/architecture.md`: package boundaries and allowed dependency flow
- `docs/quality-score.md`: subsystem quality grades and gaps
- `docs/reliability.md`: validation and operational expectations
- `docs/tech-debt.md`: tracked exceptions and cleanup backlog
- `docs/exec-plans/`: active and completed execution plans
- `core/`: iteration loop, config, analyzer, updater, git helpers
- `agent/`: LLM orchestration, evaluators, synthesis, tools
- `gateway/`: bidirectional interfaces (CLI, web, Feishu bot)
- `channels/`: outbound reporting only
- `skills/`: SKILL.md loading, matching, and progressive disclosure
- `memory/`: SQLite-backed persistence
- `web/`: Vite + Svelte frontend
- `tests/`: pytest suite
- `tasks.md`: current task board

## Non-Negotiables

- Keep repo knowledge in-repo. If context matters to future agents, write it into
  `docs/` or code comments with clear ownership.
- Respect package boundaries from `docs/architecture.md`. Prefer structural rules
  and tests over one-off reminders.
- Reuse existing helpers before adding new ones. Memory goes through
  `memory/store.py`, markdown updates through `core/updater.py`, git through
  `core/git_ops.py`.
- Skills are injected as prompt context or loaded on demand. Do not turn skills
  into ad hoc hardcoded tool behavior.
- Do not edit build artifacts such as `web/dist/` or `web/node_modules/`.

## Workflow

Before making a non-trivial change:

1. Read this file, `docs/index.md`, and the relevant module docs.
2. Check `tasks.md` and recent `git log`.
3. Prefer small, focused changes with explicit validation.

During implementation:

- Match existing patterns unless a documented architectural change is part of the task.
- Track new architectural or product decisions in `docs/`.
- Keep files near the repo targets: Python around 150-250 lines; Svelte/TS around
  100-200 lines. Track known exceptions in `docs/tech-debt.md`.

## Validation

Run before committing:

- `python -m pytest tests/ -v`
- `python -m core.docs_lint`
- `pre-commit run --all-files`

If a rule should be permanent, encode it in tests, linters, or docs lint rather
than relying on memory.

## Git Rules

- Make atomic commits with Conventional Commits: `type(scope): subject`
- Stage specific files only; do not use blind `git add .` / `git add -A`
- Do not bypass hooks with `--no-verify`
- Do not force-push `main`
- Do not commit secrets, `.env`, local DBs, or user-local config

## Quick Commands

```bash
pip install -e ".[all,dev]"
python -m pytest tests/ -v
python -m core.docs_lint
pre-commit run --all-files
evidune run -c examples/zhihu/evidune.yaml
evidune serve -c examples/zhihu/evidune.yaml
cd web && npm install && npm run build
```

## When In Doubt

Choose the smaller diff, document the decision in-repo, and leave the codebase
more legible to the next agent run than you found it.
