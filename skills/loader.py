"""SKILL.md parser — OpenClaw-compatible skill format."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Skill:
    """A loaded skill definition."""

    name: str
    description: str
    instructions: str  # The markdown body (after frontmatter)
    path: Path
    tags: list[str] = field(default_factory=list)
    outcome_metrics: bool = False  # Aiflay-specific: participates in iteration
    meta: dict[str, Any] = field(default_factory=dict)  # All other frontmatter fields


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_skill(path: str | Path) -> Skill:
    """Parse a SKILL.md file into a Skill object.

    Format:
        ---
        name: skill-name
        description: What this skill does
        tags: [tag1, tag2]
        outcome_metrics: true
        ---
        ## Instructions
        Markdown body...
    """
    path = Path(path)
    content = path.read_text(encoding="utf-8")

    match = _FRONTMATTER_RE.match(content)
    if not match:
        # No frontmatter — treat the whole file as instructions
        name = path.stem
        return Skill(
            name=name,
            description="",
            instructions=content,
            path=path,
        )

    frontmatter = yaml.safe_load(match.group(1)) or {}
    body = content[match.end() :].strip()

    return Skill(
        name=frontmatter.get("name", path.stem),
        description=frontmatter.get("description", ""),
        instructions=body,
        path=path,
        tags=frontmatter.get("tags", []),
        outcome_metrics=frontmatter.get("outcome_metrics", False),
        meta={
            k: v
            for k, v in frontmatter.items()
            if k not in ("name", "description", "tags", "outcome_metrics")
        },
    )


def load_skills_from_dir(directory: str | Path) -> list[Skill]:
    """Load all SKILL.md files from a directory (non-recursive)."""
    directory = Path(directory)
    if not directory.is_dir():
        return []

    skills = []
    for f in sorted(directory.iterdir()):
        if f.is_dir():
            skill_file = f / "SKILL.md"
            if skill_file.exists():
                skills.append(parse_skill(skill_file))
        elif f.name == "SKILL.md" or f.suffix == ".md" and f.name.endswith("-SKILL.md"):
            skills.append(parse_skill(f))
    return skills
