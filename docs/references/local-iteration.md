# Local Iteration

Use the local iteration workflow when you want to validate the full loop without
external platform adapters.

## Starter Flow

```bash
evidune init --path demo
cd demo
evidune run --config evidune.yaml
evidune iterations list --config evidune.yaml
```

## Runtime Artifacts

- `.evidune/memory.db`: shared SQLite store for conversations, facts, and iteration runs
- `.evidune/emerged_skills/`: output directory for conversation-synthesized skills
- `.evidune/runtime/<environment_id>/`: task-scoped harness environments with service state,
  structured observability, and validation artifacts

## Path Semantics

Runtime paths in `evidune.yaml` are resolved relative to the config directory for:

- `memory.path`
- `agent.emergence.output_dir`
- `metrics.config.file`
