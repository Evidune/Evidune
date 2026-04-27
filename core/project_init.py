"""Starter project scaffold for local iteration workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class InitResult:
    root: Path
    created_files: list[Path]


def _config_template(domain: str) -> str:
    return f"""domain: {domain}
description: Local starter project for a self-iterating skill agent

agent:
  llm_provider: openai
  llm_model: gpt-4o
  api_key_env: OPENAI_API_KEY
  temperature: 0.7
  system_prompt: |
    You are a general self-iterating skill agent.
    Execute the user's task with available tools, persist useful context,
    and improve reusable skills when a repeatable capability emerges.
  tools:
    external_enabled: true
  harness:
    strategy: swarm
    default_squad: general
    stream_events: true
    environment:
      runtime_dir: .evidune/runtime
    validation:
      enabled: true
      browser: playwright
    delivery:
      enabled: true
      github_enabled: false
  emergence:
    output_dir: .evidune/emerged_skills

skills:
  directories:
    - skills/
  auto_update: true

identities:
  directories:
    - identities/
  default: general-assistant

memory:
  path: .evidune/memory.db
  max_messages_per_conversation: 50

gateways:
  - type: cli

metrics:
  adapter: generic_csv
  config:
    file: data/metrics.csv
    entity_id_field: task_id
    exemplar_field: task
    timestamp_field: date
    metric_fields: [success_score, reuse_count]
    dimension_fields: [channel, outcome]
    sort_metric: success_score

references:
  - path: skills/task-execution/references/iteration-notes.md
    update_strategy: replace_section
    section: "## Strong Outcomes"

analysis:
  top_n: 3
  bottom_n: 2

iteration:
  git_commit: false
  commit_prefix: "chore(review): "

channels:
  - type: stdout
"""


_STARTER_FILES = {
    "data/metrics.csv": """task_id,task,success_score,reuse_count,date,channel,outcome
incident-fix,Investigate a failing integration and patch the config,95,4,2026-04-10,cli,verified_fix
workflow-capture,Create a reusable workflow from repeated support questions,88,3,2026-04-09,web,reusable_skill
stale-answer,Answer a current-data request without tool verification,42,0,2026-04-02,web,insufficient_evidence
""",
    "identities/general-assistant/SOUL.md": """# Soul

- Stay practical and concrete.
- Prefer verified tool output over speculation.
- Optimize for executable progress and reusable capability.
- Surface gaps, failures, and safety boundaries directly.
""",
    "identities/general-assistant/IDENTITY.md": """---
name: general-assistant
display_name: Evidune
description: General self-iterating skill agent
language: en
expertise: [task execution, code implementation, skill iteration, diagnostics]
voice: direct, precise, pragmatic
default: true
---

You are Evidune's default runtime identity.
Use loaded skills, memory, and tools to complete tasks and improve reusable
skills when repeatable patterns emerge.
""",
    "identities/general-assistant/USER.md": """# User

The user expects a working agent system: clear execution, concise reporting,
and durable improvements to skills instead of one-off advice.
""",
    "skills/task-execution/SKILL.md": """---
name: task-execution
description: Execute general user tasks with tool-backed evidence and reusable skill feedback
tags: [task-execution, verification, skill-iteration]
triggers:
  - execute this task
  - investigate and fix
  - verify with tools
  - turn this repeated workflow into a skill
update_section: "## Reference Data"
execution_contract:
  version: 1
  min_pass_score: 0.7
  rewrite_below_score: 0.55
  disable_below_score: 0.25
  min_samples_for_rewrite: 3
  min_samples_for_disable: 2
  criteria:
    - name: goal_completion
      description: The response completes the user's requested operational outcome.
      weight: 0.4
    - name: tool_grounding
      description: Claims and decisions are grounded in available tool output or explicit limits.
      weight: 0.35
    - name: durable_learning
      description: Reusable lessons are captured or routed to skill creation when appropriate.
      weight: 0.25
  observable_signals:
    - name: relevant_tool_trace
      description: Relevant tool calls or an explicit no-tool limitation are present.
      source: tool_trace
      weight: 0.3
  failure_modes:
    - skipped_required_verification
    - hallucinated_external_state
    - failed_to_capture_reusable_workflow
outcome_contract:
  entity: task
  primary_kpi: success_score
  supporting_kpis: [reuse_count]
  dimensions: [channel, outcome]
  window:
    current_days: 7
    baseline_days: 7
  min_sample_size: 3
  rewrite_policy:
    target: 90
    min_delta: 5
    require_segment: true
    severe_regression_delta: 15
  rollback_policy:
    max_negative_delta: 10
  reference_update_policy:
    max_segments: 3
    max_exemplars: 2
---

## Instructions

Handle user tasks as operational work:

1. Identify the requested outcome and constraints.
2. Use tools when verification, code changes, data retrieval, or file inspection are needed.
3. Report what was actually checked or changed.
4. If the request reveals a repeatable capability, create or update a skill package.
5. Keep durable lessons in references instead of relying on chat memory.

## Reference Data

This section is replaced by `evidune run` after each iteration cycle.
""",
    "skills/task-execution/references/iteration-notes.md": """## Strong Outcomes

This file is updated by the local iteration loop after each run.
""",
    "skills/task-execution/references/evaluation-contract.md": """# task-execution Execution Contract

## Success Criteria

- Complete the user's operational outcome.
- Ground claims in tool output, user-provided data, or explicit uncertainty.
- Capture reusable lessons in memory, references, or a skill transaction when appropriate.

## Observable Signals

- Tool trace shows relevant inspection, validation, or a clear no-tool limitation.
- Execution metadata and user feedback support whether the task was completed.

## Failure Modes

- Skipped required verification.
- Hallucinated current external state.
- Failed to capture an obviously reusable workflow.
""",
    "skills/skill-agent/SKILL.md": """---
name: skill-agent
description: Create, update, reuse, and diagnose Claude/OpenClaw-style skill packages
tags: [skills, lifecycle, emergence]
triggers:
  - create a skill
  - update a skill
  - diagnose skill matching
  - reusable capability
  - 建立 skill
  - 创建能力
execution_contract:
  version: 1
  min_pass_score: 0.7
  rewrite_below_score: 0.55
  disable_below_score: 0.25
  min_samples_for_rewrite: 3
  min_samples_for_disable: 2
  criteria:
    - name: transaction_outcome
      description: The request is resolved as created, updated, reused, queued, or failed with a concrete reason.
      weight: 0.4
    - name: package_quality
      description: Created or updated skills use SKILL.md plus Markdown references, with scripts reserved for explicit helper code.
      weight: 0.3
    - name: lifecycle_clarity
      description: The response and metadata explain lifecycle state and next availability.
      weight: 0.3
  observable_signals:
    - name: skill_creation_metadata
      description: Response metadata includes the skill creation status when a transaction occurred.
      source: execution_metadata
      weight: 0.3
  failure_modes:
    - generic_advice_instead_of_transaction
    - duplicate_skill_created
    - missing_lifecycle_reason
---

## Instructions

Treat skills as first-class runtime objects:

1. Decide whether the user wants to create, update, reuse, or debug a skill.
2. Prefer standard packages with `SKILL.md` and `references/*.md`.
3. Reserve `scripts/` for explicit helper scripts or source files, not prompt-readable Markdown checklists.
4. Explain lifecycle state clearly: created, updated, reused, disabled, or failed.
5. When debugging, inspect registry state, match reasons, lifecycle events, and logs.
""",
    "skills/skill-agent/references/evaluation-contract.md": """# skill-agent Execution Contract

## Success Criteria

- Explicit skill requests resolve through a skill transaction instead of generic advice.
- Created or updated skills use `SKILL.md` and Markdown `references/`, with `scripts/` reserved for explicit helper code.
- Lifecycle status is visible as created, updated, reused, queued, or failed with a concrete reason.

## Observable Signals

- Response metadata includes `skill_creation` when a transaction occurred.
- The skill registry and lifecycle tables reflect successful creation or update.

## Failure Modes

- Generic prose answer instead of a transaction.
- Duplicate skill created when an active similar skill exists.
- Missing lifecycle reason for failure or reuse.
""",
    "skills/code-implementation/SKILL.md": """---
name: code-implementation
description: Implement code changes, inspect files, run commands, call HTTP endpoints, and verify results with tools
tags: [coding, api-integration, debugging, verification]
triggers:
  - write code
  - modify files
  - implement an API integration
  - run tests
  - debug the service
  - 写代码
  - 修改代码
  - 接入 API
execution_contract:
  version: 1
  min_pass_score: 0.7
  rewrite_below_score: 0.55
  disable_below_score: 0.25
  min_samples_for_rewrite: 3
  min_samples_for_disable: 2
  criteria:
    - name: implementation_progress
      description: The response performs or clearly scopes concrete code changes.
      weight: 0.35
    - name: verification_quality
      description: The result is validated with relevant tests, commands, logs, or explicit blockers.
      weight: 0.35
    - name: change_safety
      description: The work preserves unrelated changes and respects tool/security boundaries.
      weight: 0.3
  observable_signals:
    - name: validation_command_recorded
      description: A validation command, test result, or explicit inability to validate is recorded.
      source: execution_metadata
      weight: 0.3
  failure_modes:
    - unverified_code_claim
    - overwrote_unrelated_changes
    - ignored_tool_boundary
---

## Instructions

Use this skill when the task requires concrete implementation:

1. Inspect the existing files before proposing changes.
2. Make focused edits and preserve unrelated work.
3. Run the narrowest useful validation first, then broader checks when needed.
4. Report exact commands, changed files, and any remaining risk.
5. If runtime tools are unavailable, state that as a blocker instead of pretending execution happened.
""",
    "skills/code-implementation/references/evaluation-contract.md": """# code-implementation Execution Contract

## Success Criteria

- Perform or precisely scope concrete code changes.
- Report validation commands, test results, logs, or explicit blockers.
- Preserve unrelated work and configured tool boundaries.

## Observable Signals

- Tool trace shows file inspection, edits, command execution, or a clear no-tool limitation.
- Execution metadata records validation status and remaining risk.

## Failure Modes

- Claimed code changes without evidence.
- Overwrote unrelated user work.
- Ignored configured shell, file, or network boundaries.
""",
}


def init_project(target_dir: str | Path) -> InitResult:
    """Create a starter project for local outcome iteration."""
    root = Path(target_dir).expanduser().resolve()
    planned = [root / "evidune.yaml", *(root / rel for rel in _STARTER_FILES)]
    existing = [path for path in planned if path.exists()]
    if existing:
        names = ", ".join(path.relative_to(root).as_posix() for path in existing)
        raise ValueError(f"Refusing to overwrite existing scaffold files: {names}")

    root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    config_path = root / "evidune.yaml"
    domain = root.name.replace(" ", "-") or "local-demo"
    config_path.write_text(_config_template(domain), encoding="utf-8")
    created.append(config_path)

    for rel_path, content in _STARTER_FILES.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created.append(path)

    return InitResult(root=root, created_files=created)
