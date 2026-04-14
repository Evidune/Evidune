# Agents Guidance for Aiflay

This document is the single source of truth for every coding assistant (Claude Code, Codex, Gemini, Cursor, etc.) that collaborates on this repository. Keep this file authoritative and avoid duplicating instructions elsewhere; all agent-specific instruction files must reference this document.

## Instruction Precedence & Mirrors

- Respect instruction order: system / developer → user → this file → everything else.
- The following files must be kept as symlinks (or exact copies) of `AGENTS.md`: `CLAUDE.md`, `GEMINI.md`, `.cursorrules`.
- When this file is updated, ensure mirrored files stay in sync within the same commit.

## Mission & Scope

Aiflay is a **lightweight AI agent framework** implementing the minimal intersection of OpenClaw + Hermes, with one unique differentiator: **outcome-driven skill self-iteration** — skills get sharpened by real-world business metrics (reads, upvotes, revenue), not execution traces.

Two modes:

- `aiflay run` — iteration loop: metrics → analysis → update skill reference docs → git commit → notify
- `aiflay serve` — interactive agent: gateways receive messages → AgentCore selects skills + loads memory → LLM responds

## Repository Layout

```
core/           Iteration subsystem (config, loop, analyzer, updater, git_ops, metrics)
adapters/       Metrics data adapters (generic_csv first; platform-specific later)
channels/       Outbound-only notification channels (stdout, feishu)
gateway/        Bidirectional message gateways (cli, feishu_bot, web)
agent/          LLM agent core (llm.py, core.py)
skills/         SKILL.md loader + registry (OpenClaw-compatible format)
memory/         SQLite persistent memory (conversations, messages, facts)
tests/          pytest suite — keep all tests passing
web/            Vite + Svelte frontend for the web gateway
examples/       Reference configs (e.g., examples/zhihu/aiflay.yaml)
```

## Atomic Commit Discipline (Critical)

- Stage and commit every atomic change **immediately** after completing validation; do not carry pending edits while starting new tasks.
- One commit = one logical change. Don't bundle unrelated edits.
- Keep the working tree clean at all times — no stray modifications or half-staged files between commits.
- If a change cannot be validated yet, park it on a feature branch instead of leaving it unstaged.

## Commit & Branch Policy

- **Conventional Commits** are enforced by `commitlint`. Format: `type(scope): subject`.
- Allowed types: `build`, `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert`, `style`, `test`.
- Subject: lowercase or sentence-case, ≤72 chars, no trailing period.
- Examples:
  - `feat(skills): support custom update_section in frontmatter`
  - `fix(gateway): handle empty Feishu event payloads`
  - `refactor(core): extract _build_skill_reference_content helper`
  - `test(memory): add facts search edge cases`
- Never use `--no-verify` to bypass hooks unless explicitly authorized.

## Code Architecture & Quality Standards

### File Size Targets (Soft Limits)

- **Python files**: target 150–250 lines. Refactor when approaching 300.
- **TypeScript / Svelte components**: target 100–200 lines. Refactor when approaching 250.
- A file exceeding the limit is technical debt — split before adding new code.

### Single Responsibility

- One file, one purpose. One service class per domain concept.
- API endpoints stay thin; business logic belongs in services or core modules.
- Svelte components separate presentation from logic; extract stores for shared state.

### Module Boundaries (do not violate)

- `gateway/` is **bidirectional** (receive + respond). `channels/` is **outbound only** (notification reports). They are not interchangeable.
- `core/loop.py` is the iteration orchestrator. New iteration steps must extend it explicitly, not be hidden in adapters.
- `agent/core.py` is the **only** place that wires LLM + skills + memory for interactive responses.
- Skills are injected as **system prompt context**, not as tool definitions. This is a deliberate v1 simplification.

### Reuse Before Adding

- Before writing new helpers, search for existing functions in the same module.
- Memory operations: use `memory/store.py` API; do not query SQLite directly elsewhere.
- Markdown updates: use `core/updater.py` strategies; do not hand-roll string replacement.
- Git operations: route through `core/git_ops.py`.

## Testing Standards

- **All tests must pass** before any commit: `python -m pytest tests/ -v`.
- Each new module ships with at least basic unit tests in `tests/test_<module>.py`.
- Skill self-iteration changes require integration tests in `tests/test_skill_iteration.py`.
- Mock external services (LLM APIs, Feishu) in tests; never call real APIs from the test suite.
- Test names describe behaviour (`test_skips_skills_without_outcome_metrics`), not implementation.

## Pre-commit Gates

The following hooks run automatically (`pre-commit install` once after clone):

1. **Trailing whitespace, EOL, YAML/JSON checks** — basic hygiene
2. **ruff** (lint + auto-fix) on Python files
3. **black** (formatting) on Python files
4. **isort** (import ordering, black profile) on Python files
5. **prettier** on JSON/YAML/Markdown/Svelte/TS/CSS files
6. **commitlint** on commit messages (conventional commits)

If a hook fails, fix the underlying issue. Do not bypass.

## AI Collaboration Workflow

### Before starting

- Read this file + the relevant module's docstrings.
- Check `tasks.md` for the current work board.
- Check `CHANGELOG.md` (if present) and recent `git log` for context.

### While working

- Use the codebase's existing patterns. Don't introduce new abstractions without justification.
- For non-trivial changes, propose a plan first; execute only after the user confirms.
- Track multi-step work with the task system. Mark tasks `in_progress` → `completed` as you go.

### Before committing

- Run the full test suite: `python -m pytest tests/`.
- Run pre-commit: `pre-commit run --all-files` (or let the commit hook run it).
- Make sure the commit message conforms to Conventional Commits.
- Update `tasks.md` if the change affects the task board.

### Forbidden

- Bypassing pre-commit hooks (`--no-verify`).
- Force-pushing to `main`.
- Committing secrets, API keys, or local config (`.env`, `~/.aiflay/`, `*.db`).
- Using `git add .` or `git add -A` blindly — stage specific files.
- Editing `web/dist/` or `web/node_modules/` directly (build artefacts).

## Skills Routing (Aiflay-specific)

- Skills with `outcome_metrics: true` in their YAML frontmatter participate in the iteration loop.
- The iteration loop replaces each such skill's `update_section` (default `## Reference Data`) with fresh Top Performers + Patterns.
- Custom section: `update_section: "## My Custom Section"` in frontmatter.
- This is **the** core differentiator vs Hermes / OpenClaw — preserve it carefully.

## Documentation

- README is the user-facing entry. Keep it concise; link to `docs/` for depth (create `docs/` if missing).
- `CLAUDE.md` is a thin pointer to this file. Don't add operational rules there.
- Architecture changes: document the _why_ in commit messages and `docs/`, not in inline code comments.

## Quick Commands

```bash
# Install (dev mode with all extras)
pip install -e ".[all,dev]"

# Run tests
python -m pytest tests/ -v
python -m pytest tests/test_skill_iteration.py -v   # single file

# Pre-commit
pre-commit install                   # one-time
pre-commit run --all-files           # manual run

# Aiflay CLI
aiflay run -c examples/zhihu/aiflay.yaml     # one iteration cycle
aiflay serve -c examples/zhihu/aiflay.yaml   # start agent

# Frontend
cd web && npm install && npm run dev         # dev server
cd web && npm run build                       # production build
```

## When in Doubt

- Default to less code, fewer abstractions, smaller diffs.
- When user intent is ambiguous, ask before implementing.
- When a change affects multiple modules, propose the plan first.
- Match scope to what was asked. Don't refactor adjacent code without permission.
