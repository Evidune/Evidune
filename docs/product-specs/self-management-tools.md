# Self-Management Tools

Evidune exposes runtime self-management as structured tools, not generated
skills. Skills may document operating policy, but configuration mutation and
restart signaling remain explicit tool calls with bounded scope.

## Tool Set

- `config_get`: read the active `evidune.yaml`, or a dotted path inside it
- `config_validate`: parse the active config through `core.config.load_config`
- `config_patch`: apply dotted-path updates after validation; defaults to
  `dry_run=true` and writes a timestamped backup when applied
- `request_runtime_restart`: write `.evidune/restart-request.json` for a
  supervisor or operator to consume

## Behavior

- Tools are registered only in execute mode through the same `ToolRegistry` as
  external tools.
- `agent.tools.self_management_enabled` controls availability and defaults to
  `true` for local-first developer preview usage.
- `config_patch` never writes an invalid config. It validates the candidate
  file before replacing the configured `evidune.yaml`.
- Applying a config patch reports `restart_required=true`; hot reload is not
  implied.
- `request_runtime_restart` does not kill the current process. The current
  `serve` runtime has no supervisor contract, so the safe primitive is a
  restart request marker.

## Non-Goals

- These tools do not synthesize, activate, or rewrite skills.
- These tools do not bypass file, shell, or git boundaries.
- These tools do not promise automatic process respawn until a gateway
  supervisor consumes the restart marker.
