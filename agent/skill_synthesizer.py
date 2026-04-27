"""Skill synthesis — generate a complete directory-based skill package.

Second half of the emergence pipeline. Given a DetectedPattern + the
conversation context, asks an LLM to write a complete Claude/OpenClaw-style
skill directory, then writes the safe markdown bundle to disk.

Output layout:
    <output_dir>/<skill-name>/SKILL.md
    <output_dir>/<skill-name>/references/*.md

The synthesised skill is activated by default. Manual review remains
available as a separate lifecycle state rather than the default gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.llm import LLMClient
from agent.pattern_detector import DetectedPattern
from agent.utils import format_conversation, strip_code_fence
from skills.evaluation import (
    default_contract_for_skill,
    parse_evaluation_contract,
    upsert_execution_contract_frontmatter,
)
from skills.loader import Skill

DEFAULT_OUTPUT_DIR = Path.home() / ".evidune" / "emerged_skills"


@dataclass
class SynthesisResult:
    name: str
    skill_md: str
    path: Path
    files: dict[str, str] = field(default_factory=dict)


_FILE_MARKER_RE = re.compile(r"^<<<FILE:\s*([^>\n]+?)\s*>>>\s*$", re.MULTILINE)

_PROMPT_TEMPLATE = """You are a skill author. Turn the conversation pattern below
into a complete, reusable standard Claude/OpenClaw directory-based skill.

# Pattern detected

- Suggested name: {name}
- Description: {description}
- Why: {rationale}

# Existing skill package

{existing_skill_block}

# Source conversation

{conversation_block}

# Output spec

Return ONLY a file bundle. Do not add surrounding prose or code fences.
Each file must start with a marker on its own line:

<<<FILE: relative/path.md>>>
file content here

Required files:
- SKILL.md
- references/checklist.md
- references/source-notes.md
- references/evaluation-contract.md

Allowed paths:
- SKILL.md
- references/*.md

Do not emit absolute paths, parent-directory paths, executable scripts, .py files,
.sh files, or files outside references/.

SKILL.md must contain:

1. YAML frontmatter with:
   - name: kebab-case identifier
   - description: one-line summary (the same as 'description' above is fine)
   - tags: list of 2-4 relevant kebab-case tags
   - triggers: list of 2-4 phrases that should activate this skill
   - anti_triggers: list of 1-3 phrases that should NOT activate it
   - execution_contract with version, criteria, observable_signals, failure_modes,
     min_pass_score, rewrite_below_score, disable_below_score,
     min_samples_for_rewrite, and min_samples_for_disable
2. ## Instructions section: 5-15 actionable rules an LLM should follow when invoked
3. ## Examples section with at least 1 example (### Example 1: ...)
4. ## Reference Data section (placeholder for future iteration)

references/*.md should contain durable background notes, source categories,
examples, checklists, workflows, or operating constraints extracted from the conversation.
references/evaluation-contract.md must explain the execution contract in human-readable
terms: success criteria, observable signals, failure modes, and thresholds.

Be concrete and useful. Do not include placeholder text like "TODO" or
"fill this in later". The skill should work on day one.
"""


def _format_conversation(history: list[dict[str, str]]) -> str:
    return format_conversation(history, max_content_length=1200)


def _strip_code_fence(raw: str) -> str:
    """If the LLM wraps the output in ```markdown ...```, strip it."""
    return strip_code_fence(raw).strip() + "\n"


def _default_checklist_reference(pattern: DetectedPattern) -> str:
    return (
        f"# {pattern.suggested_name} Checklist\n\n"
        "- Confirm the user's concrete goal and reusable context.\n"
        "- Follow the skill instructions before drafting the final answer.\n"
        "- Separate current environment limits from future integration options.\n"
        "- Produce structured output that can be reused in later conversations.\n"
    )


def _default_reference(pattern: DetectedPattern) -> str:
    description = pattern.description or "Reusable capability emerging from conversation."
    rationale = pattern.rationale or "Detected as a reusable conversation pattern."
    return (
        f"# {pattern.suggested_name} Source Notes\n\n"
        f"Description: {description}\n\n"
        f"Detection rationale: {rationale}\n"
    )


def _default_evaluation_reference(pattern: DetectedPattern) -> str:
    contract = default_contract_for_skill(pattern.suggested_name, pattern.description)
    criteria = "\n".join(f"- {item.name}: {item.description}" for item in contract.criteria)
    observables = "\n".join(
        f"- {item.name} ({item.source}): {item.description}" for item in contract.observable_metrics
    )
    failures = "\n".join(f"- {item}" for item in contract.failure_modes)
    return (
        f"# {pattern.suggested_name} Execution Contract\n\n"
        "## Success Criteria\n\n"
        f"{criteria}\n\n"
        "## Observable Signals\n\n"
        f"{observables}\n\n"
        "## Failure Modes\n\n"
        f"{failures}\n"
    )


def _format_existing_skill(skill: Skill | None) -> str:
    if skill is None:
        return "No existing skill package. Create a new skill."

    scripts = ", ".join(sorted(skill.scripts)) or "(none)"
    references = ", ".join(sorted(skill.references)) or "(none)"
    return "\n".join(
        [
            "Update the existing skill package instead of creating a duplicate.",
            f"- Existing name: {skill.name}",
            f"- Existing path: {skill.path}",
            f"- Existing scripts: {scripts}",
            f"- Existing references: {references}",
            "",
            "Current SKILL.md:",
            skill.path.read_text(encoding="utf-8"),
            "",
            "Return a complete replacement bundle. Preserve the existing skill name.",
        ]
    )


def _parse_file_bundle(raw: str, pattern: DetectedPattern) -> dict[str, str] | None:
    cleaned = strip_code_fence(raw)
    markers = list(_FILE_MARKER_RE.finditer(cleaned))
    if not markers:
        skill_md = _strip_code_fence(raw)
        if not skill_md.strip():
            return None
        files = {"SKILL.md": skill_md}
        _ensure_standard_support_files(files, pattern)
        return files

    files: dict[str, str] = {}
    for idx, marker in enumerate(markers):
        start = marker.end()
        end = markers[idx + 1].start() if idx + 1 < len(markers) else len(cleaned)
        rel_path = marker.group(1).strip()
        content = cleaned[start:end].strip()
        files[rel_path] = content + "\n"

    _ensure_standard_support_files(files, pattern)
    return files


def _ensure_standard_support_files(files: dict[str, str], pattern: DetectedPattern) -> None:
    _ensure_evaluation_contract(files, pattern)
    if "references/checklist.md" not in files:
        files["references/checklist.md"] = _default_checklist_reference(pattern)
    if "references/source-notes.md" not in files:
        files["references/source-notes.md"] = _default_reference(pattern)
    if "references/evaluation-contract.md" not in files:
        files["references/evaluation-contract.md"] = _default_evaluation_reference(pattern)


def _ensure_evaluation_contract(files: dict[str, str], pattern: DetectedPattern) -> None:
    skill_md = files.get("SKILL.md", "")
    if not skill_md.strip():
        return
    from yaml import YAMLError, safe_load

    contract = None
    if skill_md.startswith("---"):
        try:
            frontmatter = safe_load(skill_md.split("---", 2)[1]) or {}
            contract = parse_evaluation_contract(
                frontmatter.get("execution_contract") or frontmatter.get("evaluation_contract")
            )
        except (IndexError, YAMLError):
            contract = None
    if contract is None:
        contract = default_contract_for_skill(pattern.suggested_name, pattern.description)
        files["SKILL.md"] = upsert_execution_contract_frontmatter(skill_md, contract)


def _safe_bundle_path(rel_path: str) -> bool:
    if not rel_path or "\\" in rel_path:
        return False
    path = Path(rel_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    if rel_path == "SKILL.md":
        return True
    if not rel_path.endswith(".md"):
        return False
    if len(path.parts) != 2:
        return False
    return path.parts[0] == "references"


def _validate_file_bundle(files: dict[str, str]) -> bool:
    if "SKILL.md" not in files or not files["SKILL.md"].strip():
        return False
    for rel_path, content in files.items():
        if not _safe_bundle_path(rel_path) or not content.strip():
            return False
    return True


class SkillSynthesizer:
    """LLM-driven generator of full SKILL.md from a detected pattern."""

    def __init__(
        self,
        judge: LLMClient,
        output_dir: str | Path | None = None,
    ) -> None:
        self.judge = judge
        self.output_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR

    async def synthesize(
        self,
        pattern: DetectedPattern,
        history: list[dict[str, str]],
        write: bool = True,
        existing_skill: Skill | None = None,
        **llm_kwargs: Any,
    ) -> SynthesisResult | None:
        """Generate and (optionally) persist a directory-based skill package.

        Returns None if the pattern is not a skill or the LLM returned
        empty or unsafe content.
        """
        if not pattern.is_skill or not pattern.suggested_name:
            return None

        target_name = existing_skill.name if existing_skill is not None else pattern.suggested_name
        prompt = _PROMPT_TEMPLATE.format(
            name=target_name,
            description=pattern.description or "(none)",
            rationale=pattern.rationale or "(none)",
            existing_skill_block=_format_existing_skill(existing_skill),
            conversation_block=_format_conversation(history),
        )
        kwargs = {"temperature": 0.3, **llm_kwargs}
        raw = await self.judge.complete(
            [{"role": "user", "content": prompt}],
            **kwargs,
        )

        files = _parse_file_bundle(raw, pattern)
        if files is None or not _validate_file_bundle(files):
            return None

        skill_dir = (
            existing_skill.root if existing_skill is not None else self.output_dir / target_name
        )
        skill_path = skill_dir / "SKILL.md"

        if write:
            for rel_path, content in files.items():
                target = skill_dir / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content.strip() + "\n", encoding="utf-8")

        return SynthesisResult(
            name=target_name,
            skill_md=files["SKILL.md"],
            path=skill_path,
            files=files,
        )
