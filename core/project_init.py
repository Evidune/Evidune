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
    title_field: task
    metric_fields: [success_score, reuse_count]
    metadata_fields: [date, outcome]
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
    "data/metrics.csv": """task,success_score,reuse_count,date,outcome
Investigate a failing integration and patch the config,95,4,2026-04-01,verified_fix
Create a reusable workflow from repeated support questions,88,3,2026-04-03,reusable_skill
Answer a current-data request without tool verification,42,0,2026-04-05,insufficient_evidence
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
outcome_metrics: true
update_section: "## Reference Data"
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
outcome_metrics: false
---

## Instructions

Treat skills as first-class runtime objects:

1. Decide whether the user wants to create, update, reuse, or debug a skill.
2. Prefer standard packages with `SKILL.md`, optional `scripts/*.md`, and `references/*.md`.
3. Keep generated scripts prompt-readable unless the user explicitly asks for executable code.
4. Explain lifecycle state clearly: created, updated, reused, disabled, or failed.
5. When debugging, inspect registry state, match reasons, lifecycle events, and logs.
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
outcome_metrics: false
---

## Instructions

Use this skill when the task requires concrete implementation:

1. Inspect the existing files before proposing changes.
2. Make focused edits and preserve unrelated work.
3. Run the narrowest useful validation first, then broader checks when needed.
4. Report exact commands, changed files, and any remaining risk.
5. If runtime tools are unavailable, state that as a blocker instead of pretending execution happened.
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
