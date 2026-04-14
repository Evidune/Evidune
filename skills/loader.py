"""Skill parser — Claude/OpenClaw-compatible directory-based skill format.

A skill can be:
  - A directory containing SKILL.md (preferred, full Claude format):
      my-skill/
        SKILL.md           ← main file (frontmatter + markdown body)
        references/        ← optional: detailed reference documents
          advanced.md
        scripts/           ← optional: executable helpers
          helper.py
        assets/            ← optional: templates, boilerplate
          template.json
  - A bare SKILL.md file (compatibility mode, no subdirectories).

SKILL.md frontmatter supports:
  name, description, version, tags, triggers, anti_triggers,
  outcome_metrics, update_section, plus arbitrary meta fields.

The markdown body may contain "## Triggers", "## Anti-Triggers",
and "## Examples" sections that augment the frontmatter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


@dataclass
class Skill:
    """A loaded skill definition (Claude-style directory layout)."""

    name: str
    description: str
    path: Path  # Path to SKILL.md
    version: str = "1.0.0"
    tags: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    anti_triggers: list[str] = field(default_factory=list)
    outcome_metrics: bool = False  # Aiflay-specific: participates in iteration
    update_section: str = "## Reference Data"
    instructions: str = ""  # Full markdown body of SKILL.md
    examples: list[str] = field(default_factory=list)  # Parsed "## Examples" entries
    references: dict[str, str] = field(default_factory=dict)  # filename → content
    scripts: dict[str, Path] = field(default_factory=dict)  # filename → path
    assets: dict[str, Path] = field(default_factory=dict)  # filename → path
    meta: dict[str, Any] = field(default_factory=dict)  # remaining frontmatter

    @property
    def root(self) -> Path:
        """Directory containing SKILL.md (for directory-based skills)."""
        return self.path.parent


def _extract_section(body: str, heading: str) -> str | None:
    """Extract the content of a markdown section by heading text.

    Returns the body of the section (without the heading line),
    stopping at the next same-or-higher-level heading.
    """
    target = heading.lstrip("#").strip()
    lines = body.split("\n")
    start_idx: int | None = None
    start_level: int | None = None

    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if not m:
            continue
        level = len(m.group(1))
        title = m.group(2).strip()
        if start_idx is None and title == target:
            start_idx = i
            start_level = level
        elif start_idx is not None and level <= (start_level or 0):
            return "\n".join(lines[start_idx + 1 : i]).strip()

    if start_idx is not None:
        return "\n".join(lines[start_idx + 1 :]).strip()
    return None


def _parse_list_section(body: str, heading: str) -> list[str]:
    """Parse a markdown section as a list of bullet items."""
    section = _extract_section(body, heading)
    if not section:
        return []
    items = []
    for line in section.split("\n"):
        line = line.strip()
        if line.startswith(("- ", "* ")):
            items.append(line[2:].strip())
        elif re.match(r"^\d+\.\s+", line):
            items.append(re.sub(r"^\d+\.\s+", "", line).strip())
    return items


def _parse_examples_section(body: str) -> list[str]:
    """Parse the ## Examples section into individual example blocks.

    Each example is a sub-section (### heading) within ## Examples.
    Returns a list of example markdown strings.
    """
    section = _extract_section(body, "## Examples")
    if not section:
        return []

    examples = []
    current: list[str] = []
    for line in section.split("\n"):
        if line.startswith("### "):
            if current:
                examples.append("\n".join(current).strip())
                current = []
            current.append(line)
        elif current:
            current.append(line)

    if current:
        examples.append("\n".join(current).strip())

    return [e for e in examples if e]


def _load_directory_resources(
    skill_root: Path,
) -> tuple[dict[str, str], dict[str, Path], dict[str, Path]]:
    """Load references/, scripts/, assets/ subdirectories of a skill.

    Returns (references_content, scripts_paths, assets_paths).
    """
    references: dict[str, str] = {}
    scripts: dict[str, Path] = {}
    assets: dict[str, Path] = {}

    refs_dir = skill_root / "references"
    if refs_dir.is_dir():
        for f in sorted(refs_dir.rglob("*")):
            if f.is_file():
                rel = f.relative_to(refs_dir).as_posix()
                try:
                    references[rel] = f.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    pass  # Skip binary or unreadable files

    scripts_dir = skill_root / "scripts"
    if scripts_dir.is_dir():
        for f in sorted(scripts_dir.rglob("*")):
            if f.is_file():
                scripts[f.relative_to(scripts_dir).as_posix()] = f

    assets_dir = skill_root / "assets"
    if assets_dir.is_dir():
        for f in sorted(assets_dir.rglob("*")):
            if f.is_file():
                assets[f.relative_to(assets_dir).as_posix()] = f

    return references, scripts, assets


def parse_skill(path: str | Path) -> Skill:
    """Parse a SKILL.md file into a Skill object.

    If the SKILL.md lives in a directory with references/, scripts/,
    or assets/ subdirectories, those are loaded too.
    """
    path = Path(path)
    content = path.read_text(encoding="utf-8")

    match = _FRONTMATTER_RE.match(content)
    if not match:
        # No frontmatter — minimal skill
        return Skill(
            name=path.stem,
            description="",
            path=path,
            instructions=content,
        )

    frontmatter = yaml.safe_load(match.group(1)) or {}
    body = content[match.end() :].strip()

    # Frontmatter triggers/anti_triggers take priority; markdown sections supplement
    fm_triggers = frontmatter.get("triggers", []) or []
    fm_anti = frontmatter.get("anti_triggers", []) or []
    md_triggers = _parse_list_section(body, "## Triggers")
    md_anti = _parse_list_section(body, "## Anti-Triggers")

    # Merge while preserving order, dedup
    triggers = list(dict.fromkeys([*fm_triggers, *md_triggers]))
    anti_triggers = list(dict.fromkeys([*fm_anti, *md_anti]))

    examples = _parse_examples_section(body)

    # Load directory resources if SKILL.md is inside a skill directory
    skill_root = path.parent
    references, scripts, assets = _load_directory_resources(skill_root)

    known_keys = {
        "name",
        "description",
        "version",
        "tags",
        "triggers",
        "anti_triggers",
        "outcome_metrics",
        "update_section",
    }

    return Skill(
        name=frontmatter.get("name", path.stem),
        description=frontmatter.get("description", ""),
        path=path,
        version=str(frontmatter.get("version", "1.0.0")),
        tags=frontmatter.get("tags", []) or [],
        triggers=triggers,
        anti_triggers=anti_triggers,
        outcome_metrics=frontmatter.get("outcome_metrics", False),
        update_section=frontmatter.get("update_section", "## Reference Data"),
        instructions=body,
        examples=examples,
        references=references,
        scripts=scripts,
        assets=assets,
        meta={k: v for k, v in frontmatter.items() if k not in known_keys},
    )


def load_skills_from_dir(directory: str | Path) -> list[Skill]:
    """Load all skills from a directory.

    Recognized layouts:
      - <dir>/<skill-name>/SKILL.md   (preferred, full Claude-style)
      - <dir>/SKILL.md                (single inline skill)
      - <dir>/<name>-SKILL.md         (legacy compat)
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []

    skills = []
    for f in sorted(directory.iterdir()):
        if f.is_dir():
            skill_file = f / "SKILL.md"
            if skill_file.exists():
                skills.append(parse_skill(skill_file))
        elif f.name == "SKILL.md" or (f.suffix == ".md" and f.name.endswith("-SKILL.md")):
            skills.append(parse_skill(f))
    return skills
