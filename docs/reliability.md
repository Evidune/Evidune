# Reliability

## Baseline Gates

Every code change should be validated with:

- `python -m pytest tests/ -v`
- `python -m core.docs_lint`
- `pre-commit run --all-files`

When frontend behavior changes, also run:

- `cd web && npm run build`

## Browser Validation

Run the deterministic browser harness for the web gateway with:

- `pip install -e ".[all,dev]"`
- `python -m playwright install chromium`
- `cd web && npm ci && npm run build`
- `python -m pytest tests/test_web_e2e.py -v`

## Reliability Principles

- Prefer deterministic local checks over manual inspection.
- Fail fast on repo drift: docs structure, mirror parity, and package boundaries
  should be checked automatically.
- Use repository-local artifacts for context. If a failure only exists in chat,
  the next agent cannot debug it.

## Next Reliability Milestones

- Expose logs and metrics to the agent through stable local interfaces.
- Expand browser-driven validation beyond execute / plan / feedback flows.
- Keep starter scaffolds and bundled examples runnable in CI-style local checks.
