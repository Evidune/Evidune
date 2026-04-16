# Reliability

## Baseline Gates

Every code change should be validated with:

- `python -m pytest tests/ -v`
- `python -m core.docs_lint`
- `pre-commit run --all-files`

When frontend behavior changes, also run:

- `cd web && npm run build`

## Reliability Principles

- Prefer deterministic local checks over manual inspection.
- Fail fast on repo drift: docs structure, mirror parity, and package boundaries
  should be checked automatically.
- Use repository-local artifacts for context. If a failure only exists in chat,
  the next agent cannot debug it.

## Next Reliability Milestones

- Add browser-driven validation for the web gateway.
- Expose logs and metrics to the agent through stable local interfaces.
- Keep starter scaffolds and bundled examples runnable in CI-style local checks.
