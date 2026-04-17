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

## Harness Runtime

Task-scoped harness environments live under `.aiflay/runtime/<environment_id>/` and must include:

- `memory.db`: isolated runtime memory for the environment
- `artifacts/`: screenshots and validation outputs
- `observability/`: structured `logs.jsonl`, `metrics.jsonl`, and `traces.jsonl`
- `services/`: lifecycle state and captured service stdout/stderr

Use these commands to operate the local runtime:

- `aiflay env up --config aiflay.yaml`
- `aiflay env status <environment_id> --config aiflay.yaml`
- `aiflay env health <environment_id> --config aiflay.yaml`
- `aiflay env restart <environment_id> --config aiflay.yaml`
- `aiflay env down <environment_id> --config aiflay.yaml`

## Validation and Delivery

- `aiflay validate run --config aiflay.yaml --page-path / --visible-test-id chat-input`
- `aiflay delivery submit --config aiflay.yaml --message "chore(harness): deliver task"`
- `aiflay maintenance sweep --config aiflay.yaml`

Validation evidence should be recorded as harness artifacts before critique, including:

- UI snapshots
- screenshots
- assertion results
- relevant structured logs / metrics / traces

Delivery must remain structured even when GitHub is unavailable:

- create or reuse a branch
- stage a targeted file set or tracked changes
- create a commit when there is a diff
- return a delivery summary with `mode=local` when PR / CI are unavailable
