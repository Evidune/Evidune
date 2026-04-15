# Aiflay

Outcome-driven skill self-iteration framework for AI agents.

Hermes optimizes what the agent _thinks_ worked. Aiflay optimizes what
_actually_ worked.

## Install

```bash
pip install aiflay
```

## Quick Start

```bash
aiflay run --config aiflay.yaml
aiflay serve --config aiflay.yaml
```

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
