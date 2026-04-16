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
description: Local starter project for outcome-driven skill iteration

agent:
  llm_provider: openai
  llm_model: gpt-4o
  api_key_env: OPENAI_API_KEY
  temperature: 0.7
  system_prompt: |
    You are a practical assistant helping the user improve content with reusable skills.
  emergence:
    enabled: false
    output_dir: .aiflay/emerged_skills

skills:
  directories:
    - skills/
  auto_update: true

identities:
  directories:
    - identities/
  default: local-writer

memory:
  path: .aiflay/memory.db
  max_messages_per_conversation: 50

gateways:
  - type: cli

metrics:
  adapter: generic_csv
  config:
    file: data/metrics.csv
    title_field: title
    metric_fields: [reads, upvotes]
    metadata_fields: [date, url]
    sort_metric: reads

references:
  - path: skills/write-article/references/case-studies.md
    update_strategy: replace_section
    section: "## Top Performers"

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
    "data/metrics.csv": """title,reads,upvotes,date,url
How I Turned a Messy Draft into a Useful Article,5200,180,2026-04-01,https://example.com/posts/1
The Simple Writing Habit That Doubled My Output,3400,121,2026-04-03,https://example.com/posts/2
Why Most Articles Feel Generic by Paragraph Three,900,21,2026-04-05,https://example.com/posts/3
""",
    "identities/local-writer/SOUL.md": """# Soul

- Stay practical and concrete.
- Prefer clear tradeoffs over empty encouragement.
- Optimize for useful output, not ornamental phrasing.
""",
    "identities/local-writer/IDENTITY.md": """# Identity

You are the local writer identity for an outcome-driven content iteration loop.
Use the loaded skill set, recent metrics, and reference material to help the user
produce stronger content and sharpen the skill docs over time.
""",
    "identities/local-writer/USER.md": """# User

The user is iterating on repeatable content patterns. They value direct feedback,
clear structure, and measurable improvement over abstract brainstorming.
""",
    "skills/write-article/SKILL.md": """---
name: write-article
description: Write a practical long-form article that can improve through metrics
outcome_metrics: true
update_section: "## Reference Data"
---

## Instructions

Write a concrete article that is specific, readable, and useful:

1. Open with a clear problem, tension, or observation.
2. Use direct language and at least one concrete example.
3. Avoid padded abstractions and vague motivational phrasing.
4. End with a takeaway the reader can act on.

## Reference Data

This section is replaced by `aiflay run` after each iteration cycle.
""",
    "skills/write-article/references/case-studies.md": """## Top Performers

This file is updated by the local iteration loop after each run.
""",
}


def init_project(target_dir: str | Path) -> InitResult:
    """Create a starter project for local outcome iteration."""
    root = Path(target_dir).expanduser().resolve()
    planned = [root / "aiflay.yaml", *(root / rel for rel in _STARTER_FILES)]
    existing = [path for path in planned if path.exists()]
    if existing:
        names = ", ".join(path.relative_to(root).as_posix() for path in existing)
        raise ValueError(f"Refusing to overwrite existing scaffold files: {names}")

    root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    config_path = root / "aiflay.yaml"
    domain = root.name.replace(" ", "-") or "local-demo"
    config_path.write_text(_config_template(domain), encoding="utf-8")
    created.append(config_path)

    for rel_path, content in _STARTER_FILES.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created.append(path)

    return InitResult(root=root, created_files=created)
