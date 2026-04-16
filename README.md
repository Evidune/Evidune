# Aiflay

Outcome-driven skill self-iteration framework for AI agents.

Hermes optimizes what the agent _thinks_ worked. Aiflay optimizes what
_actually_ worked.

## Install

```bash
pip install aiflay
```

## Quick Start

Scaffold a local starter project:

```bash
aiflay init --path demo
cd demo
aiflay run --config aiflay.yaml
```

Run the bundled Zhihu example from the repo root:

```bash
python -m core.loop run --config examples/zhihu/aiflay.yaml
python -m core.loop iterations list --config examples/zhihu/aiflay.yaml
```

Start the interactive agent:

```bash
aiflay run --config aiflay.yaml
aiflay serve --config aiflay.yaml
```

## Local Iteration

- `aiflay init` creates a runnable local loop with sample metrics, one identity, one
  outcome-tracked skill, and worktree-local runtime artifacts under `.aiflay/`.
- `aiflay run` now records each iteration cycle into SQLite so you can inspect recent
  runs with `aiflay iterations list` and `aiflay iterations show <id>`.
- Relative runtime paths like `memory.path`, `agent.emergence.output_dir`, and
  `metrics.config.file` are resolved relative to the active `aiflay.yaml`.

## Repository Docs

- [docs/index.md](docs/index.md) is the documentation hub
- [docs/architecture.md](docs/architecture.md) defines package boundaries
- [AGENTS.md](AGENTS.md) is the short entrypoint for coding agents

## Validation

```bash
python -m pytest tests/ -v
python -m core.docs_lint
pre-commit run --all-files
```
