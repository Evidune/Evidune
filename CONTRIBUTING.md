# Contributing to Evidune

Thank you for your interest in contributing! This guide covers everything you need to start.

## Setup

```bash
# Clone and install
git clone git@github.com:Evidune/Evidune.git
cd Evidune
pip install -e ".[all,dev]"

# Install pre-commit hooks (one-time)
pre-commit install
pre-commit install --hook-type commit-msg

# Frontend
cd web && npm install
```

## Read First

- [`AGENTS.md`](./AGENTS.md) — single source of truth for code style, architecture, commit conventions.
- [`tasks.md`](./tasks.md) — current work board.

## Workflow

1. **Pick or open an issue.** For non-trivial work, propose an approach first.
2. **Branch.** `git checkout -b feat/short-description` or `fix/...`.
3. **Code.** Follow the patterns in `AGENTS.md`. Keep diffs focused.
4. **Test.** `python -m pytest tests/`. Add new tests when you add new behaviour.
5. **Commit.** Conventional Commits format: `type(scope): subject`. Pre-commit hooks will validate.
6. **Push & PR.** Reference the issue. Describe what changed and why.

## Commit Message Format

Enforced by `commitlint`:

```
type(scope): short imperative subject

Optional longer body explaining the *why*, wrapped at 72 chars.
```

Allowed types: `build`, `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert`, `style`, `test`.

Examples:

```
feat(skills): support custom update_section in frontmatter
fix(gateway): handle empty Feishu event payloads
test(memory): add facts search edge cases
docs(readme): clarify outcome-driven iteration
```

## Quality Gates

The following run automatically on commit (`pre-commit`):

- `ruff` — Python linting + auto-fix
- `black` — Python formatting
- `isort` — import ordering (black profile)
- `prettier` — JSON / YAML / Markdown / Svelte
- `commitlint` — Conventional Commits validation

If a hook fails, fix the underlying issue. Do not bypass with `--no-verify`.

## File Size Targets

- Python files: 150–250 lines (refactor at 300).
- Svelte / TS components: 100–200 lines (refactor at 250).

## Reporting Issues

Search existing issues first. When opening a new one:

- Use a clear, specific title.
- Include reproduction steps, expected vs actual behaviour.
- Attach minimal config / data when relevant.

## Code of Conduct

Be respectful. Disagree on ideas, not on people.
