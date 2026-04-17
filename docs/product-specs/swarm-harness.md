# Swarm Harness

This spec defines the v1 swarm harness used by both `aiflay serve` and
`aiflay run`.

## Goals

- Treat a bounded multi-role task, not a single model completion, as the unit of work
- Keep long-running work auditable through task, step, and artifact records
- Restrict context and tool access per role so workers do not receive the whole world by default
- Preserve a backward-compatible single-agent path for simple turns

## Serve Behavior

When `agent.harness.strategy` is `swarm`, the agent routes non-trivial turns
through a deterministic harness.

- Simple turns still use the legacy single-agent path
- Multi-step, research, execution, or plan-mode turns create a harness task
- Built-in squad profiles are `general`, `research`, and `execution`
- A conversation persists the chosen `squad_profile` so follow-up turns can reuse it

Each swarm task follows these phases:

1. `plan`
2. `execute`
3. `validate`
4. `critique`
5. `finalise`

The v1 role set is fixed:

- `planner`
- `worker-1`
- optional `worker-2`
- `critic`
- `synthesizer`

## Context and Tool Rules

- The planner receives the task brief, facts, history, and prior accepted artifacts
- Each worker receives only its assigned skill subset plus accepted upstream artifacts
- The critic receives worker outputs, not the full skill registry
- The synthesizer receives the accepted worker outputs and convergence summary

Tool access is role-scoped:

- planner: internal read-only tools only
- worker: internal tools plus external tools in execute mode
- critic: internal read-only tools only
- synthesizer: no tools

## Persistence

Swarm execution is durable through these tables:

- `harness_tasks`
- `harness_steps`
- `harness_artifacts`
- `squad_profiles`

Skill executions may link back to a swarm task through `harness_task_id`.

Each harness task also persists:

- `environment_id`
- `environment_status`
- `artifact_manifest`
- `validation_summary`
- `delivery_summary`
- `escalation_reason`

Runtime environments are task-scoped and rooted at `.aiflay/runtime/<environment_id>/`.
They expose structured logs, metrics, and traces through harness tools rather than free-form shell output.

Worker roles in execute mode receive structured harness tools for:

- environment lifecycle
- UI validation
- observability queries
- delivery submission
- maintenance sweeps

## Web Surface

The web gateway keeps `POST /api/chat` as the compatibility path and adds
`GET /api/chat/stream` for SSE updates.

Swarm responses expose:

- `task_id`
- `squad`
- `task_status`
- `task_events`
- `convergence_summary`
- `budget_summary`
- `environment_id`
- `environment_status`
- `validation_summary`
- `delivery_summary`
- `artifact_manifest`

The chat UI shows the swarm timeline and grouped tool traces inline with the
assistant message.
