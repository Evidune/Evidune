# Local Iteration

Use the local iteration workflow when you want to validate the full loop without
external platform adapters.

## Starter Flow

```bash
aiflay init --path demo
cd demo
aiflay run --config aiflay.yaml
aiflay iterations list --config aiflay.yaml
```

## Runtime Artifacts

- `.aiflay/memory.db`: shared SQLite store for conversations, facts, and iteration runs
- `.aiflay/emerged_skills/`: output directory for conversation-synthesized skills when enabled
- `.aiflay/runtime/<environment_id>/`: task-scoped harness environments with service state,
  structured observability, and validation artifacts

## Path Semantics

Runtime paths in `aiflay.yaml` are resolved relative to the config directory for:

- `memory.path`
- `agent.emergence.output_dir`
- `metrics.config.file`
