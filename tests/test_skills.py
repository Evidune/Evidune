"""Tests for skills/loader.py and skills/registry.py."""

from pathlib import Path

import pytest

from skills.loader import load_skills_from_dir, parse_skill
from skills.registry import SkillRegistry


def _write_skill(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


SAMPLE_SKILL = """---
name: write-article
description: Write a compelling long-form article
tags: [writing, long-form, content]
outcome_contract:
  entity: article
  primary_kpi: reads
  supporting_kpis: [upvotes]
  dimensions: [channel]
  window:
    current_days: 7
    baseline_days: 7
  min_sample_size: 3
---

## Instructions

Write an article that follows these guidelines:
- Be practical and concrete
- Use real examples
"""

BARE_SKILL = """# No Frontmatter Skill

Just instructions, no YAML header.
"""

CLAUDE_STYLE_SKILL = """---
name: api-helper
description: Help build with the Anthropic API
version: 2.0.0
tags: [api, anthropic]
triggers:
  - user imports anthropic SDK
  - user asks about prompt caching
anti_triggers:
  - user imports openai SDK
  - task is about general programming
update_section: "## Reference Data"
---

## Instructions

Build apps with the Anthropic API. Always include prompt caching.

## Triggers

When to use:

- File imports `anthropic` package
- User mentions Claude models

## Anti-Triggers

When NOT to use:

- File imports `openai`
- Task is plain Python with no AI

## Examples

### Example 1: Add caching

Input: existing call without caching
Output: same call with cache_control headers

### Example 2: Streaming

Input: synchronous client
Output: AsyncAnthropic with stream=True
"""


class TestParseSkill:
    def test_full_skill(self, tmp_path: Path):
        path = _write_skill(tmp_path / "SKILL.md", SAMPLE_SKILL)
        skill = parse_skill(path)
        assert skill.name == "write-article"
        assert skill.description == "Write a compelling long-form article"
        assert "writing" in skill.tags
        assert skill.outcome_contract is not None
        assert skill.participates_in_outcome_governance is True
        assert skill.outcome_contract.primary_kpi == "reads"
        assert "Be practical" in skill.instructions
        assert skill.version == "1.0.0"  # default
        assert skill.update_section == "## Reference Data"  # default

    def test_bare_skill(self, tmp_path: Path):
        path = _write_skill(tmp_path / "SKILL.md", BARE_SKILL)
        skill = parse_skill(path)
        assert skill.name == "SKILL"
        assert skill.description == ""
        assert "No Frontmatter" in skill.instructions

    def test_extra_meta_preserved(self, tmp_path: Path):
        content = "---\nname: test\ncustom_field: hello\n---\nBody"
        path = _write_skill(tmp_path / "SKILL.md", content)
        skill = parse_skill(path)
        assert skill.meta.get("custom_field") == "hello"

    def test_claude_style_full_parse(self, tmp_path: Path):
        path = _write_skill(tmp_path / "api-helper" / "SKILL.md", CLAUDE_STYLE_SKILL)
        skill = parse_skill(path)
        assert skill.version == "2.0.0"
        assert "user imports anthropic SDK" in skill.triggers
        assert "user asks about prompt caching" in skill.triggers
        # Markdown ## Triggers section is also merged
        assert "File imports `anthropic` package" in skill.triggers
        assert "user imports openai SDK" in skill.anti_triggers
        assert "File imports `openai`" in skill.anti_triggers
        # Examples parsed
        assert len(skill.examples) == 2
        assert "Example 1: Add caching" in skill.examples[0]
        assert "Example 2: Streaming" in skill.examples[1]

    def test_triggers_dedup(self, tmp_path: Path):
        # Same trigger in frontmatter and markdown should appear once
        content = """---
name: dup
description: dup test
triggers:
  - same trigger
---

## Triggers

- same trigger
- different trigger
"""
        path = _write_skill(tmp_path / "SKILL.md", content)
        skill = parse_skill(path)
        assert skill.triggers.count("same trigger") == 1
        assert "different trigger" in skill.triggers

    def test_loads_references_directory(self, tmp_path: Path):
        skill_dir = tmp_path / "with-refs"
        _write_skill(skill_dir / "SKILL.md", SAMPLE_SKILL)
        _write_skill(
            skill_dir / "references" / "advanced.md", "# Advanced topic\nDeep dive content"
        )
        _write_skill(skill_dir / "references" / "examples.md", "# Examples\nMore samples")

        skill = parse_skill(skill_dir / "SKILL.md")
        assert "advanced.md" in skill.references
        assert "Deep dive content" in skill.references["advanced.md"]
        assert "examples.md" in skill.references

    def test_loads_scripts_and_assets(self, tmp_path: Path):
        skill_dir = tmp_path / "with-resources"
        _write_skill(skill_dir / "SKILL.md", SAMPLE_SKILL)
        _write_skill(skill_dir / "scripts" / "helper.py", "def hi(): pass")
        _write_skill(skill_dir / "assets" / "template.json", '{"a": 1}')

        skill = parse_skill(skill_dir / "SKILL.md")
        assert "helper.py" in skill.scripts
        assert skill.scripts["helper.py"].name == "helper.py"
        assert "template.json" in skill.assets

    def test_skill_root_property(self, tmp_path: Path):
        skill_dir = tmp_path / "rooted"
        path = _write_skill(skill_dir / "SKILL.md", SAMPLE_SKILL)
        skill = parse_skill(path)
        assert skill.root == skill_dir

    def test_custom_update_section(self, tmp_path: Path):
        content = """---
name: custom
description: custom
update_section: "## My Custom Section"
---
Body
"""
        path = _write_skill(tmp_path / "SKILL.md", content)
        skill = parse_skill(path)
        assert skill.update_section == "## My Custom Section"

    def test_execution_contract_frontmatter(self, tmp_path: Path):
        content = """---
name: triage
description: Triage incidents
execution_contract:
  version: 1
  min_pass_score: 0.7
  rewrite_below_score: 0.55
  disable_below_score: 0.25
  min_samples_for_rewrite: 3
  min_samples_for_disable: 2
  criteria:
    - name: goal_completion
      description: Completes the triage outcome
      weight: 0.6
    - name: evidence_quality
      description: Uses evidence
      weight: 0.4
  observable_signals:
    - name: tool_verification_used
      description: Tool evidence was checked
      source: tool_trace
      weight: 0.2
  failure_modes:
    - skipped_required_verification
custom_field: hello
---

## Instructions
Do triage.
"""
        path = _write_skill(tmp_path / "SKILL.md", content)
        skill = parse_skill(path)
        assert skill.execution_contract is not None
        assert skill.execution_contract.criteria[0].name == "goal_completion"
        assert skill.execution_contract.observable_signals[0].source == "tool_trace"
        assert "skipped_required_verification" in skill.execution_contract.failure_modes
        assert skill.meta["custom_field"] == "hello"

    def test_legacy_evaluation_contract_reads_as_execution_contract(self, tmp_path: Path):
        content = """---
name: triage
description: Triage incidents
evaluation_contract:
  version: 1
  criteria:
    - name: goal_completion
      description: Completes the triage outcome
      weight: 1.0
---
## Instructions
Do triage.
"""
        skill = parse_skill(_write_skill(tmp_path / "SKILL.md", content))
        assert skill.execution_contract is not None
        assert skill.execution_contract.criteria[0].name == "goal_completion"

    def test_legacy_outcome_metrics_flag_is_deprecated_without_contract(self, tmp_path: Path):
        content = """---
name: legacy
description: Legacy outcome skill
outcome_metrics: true
---
## Instructions
Legacy.
"""
        skill = parse_skill(_write_skill(tmp_path / "SKILL.md", content))
        assert skill.outcome_contract is None
        assert skill.deprecations
        assert "deprecated" in skill.deprecations[0]


class TestLoadSkillsFromDir:
    def test_loads_subdirectory_skills(self, tmp_path: Path):
        _write_skill(tmp_path / "skill-a" / "SKILL.md", SAMPLE_SKILL)
        _write_skill(tmp_path / "skill-b" / "SKILL.md", BARE_SKILL)
        skills = load_skills_from_dir(tmp_path)
        assert len(skills) == 2

    def test_empty_dir(self, tmp_path: Path):
        skills = load_skills_from_dir(tmp_path)
        assert skills == []

    def test_nonexistent_dir(self):
        skills = load_skills_from_dir("/nonexistent")
        assert skills == []


class TestSkillRegistry:
    @pytest.fixture
    def registry(self, tmp_path: Path) -> SkillRegistry:
        _write_skill(tmp_path / "writing" / "SKILL.md", SAMPLE_SKILL)
        _write_skill(
            tmp_path / "review" / "SKILL.md",
            "---\nname: review-data\ndescription: Review analytics data\ntags: [analytics, review]\n---\nReview instructions",
        )
        reg = SkillRegistry()
        reg.load_directory(tmp_path)
        return reg

    def test_all_skills(self, registry: SkillRegistry):
        assert len(registry.all()) == 2

    def test_get_by_name(self, registry: SkillRegistry):
        skill = registry.get("write-article")
        assert skill is not None
        assert skill.name == "write-article"

    def test_get_outcome_skills(self, registry: SkillRegistry):
        outcome = registry.get_outcome_skills()
        assert len(outcome) == 1
        assert outcome[0].name == "write-article"

    def test_find_relevant(self, registry: SkillRegistry):
        results = registry.find_relevant("write a long-form article")
        assert len(results) > 0
        assert results[0].name == "write-article"

    def test_find_relevant_by_tag(self, registry: SkillRegistry):
        results = registry.find_relevant("analytics review")
        assert any(s.name == "review-data" for s in results)

    def test_as_full_prompt(self, registry: SkillRegistry):
        prompt = registry.as_full_prompt()
        assert "write-article" in prompt
        assert "review-data" in prompt
        assert "# Active Skills" in prompt

    def test_as_index_prompt_compact(self, registry: SkillRegistry):
        prompt = registry.as_index_prompt()
        assert "write-article" in prompt
        assert "Write a compelling" in prompt
        assert "get_skill" in prompt
        # Index should NOT include full instructions
        assert "Be practical and concrete" not in prompt

    def test_register_direct(self, tmp_path: Path):
        path = _write_skill(tmp_path / "SKILL.md", SAMPLE_SKILL)
        skill = parse_skill(path)
        reg = SkillRegistry()
        reg.register(skill)
        assert reg.get("write-article") is skill

    def test_records_expose_first_class_skill_metadata(self, registry: SkillRegistry):
        records = {record.name: record for record in registry.records()}
        record = records["write-article"]
        assert record.source == "base"
        assert record.status == "active"
        assert record.path.endswith("SKILL.md")
        assert "writing" in record.tags

    def test_records_expose_execution_contract_summary(self, tmp_path: Path):
        _write_skill(
            tmp_path / "triage" / "SKILL.md",
            """---
name: triage
description: Triage incidents
execution_contract:
  version: 1
  criteria:
    - name: goal_completion
      description: Completes triage
      weight: 1.0
  observable_signals:
    - name: tool_verification_used
      description: Tool evidence was checked
      source: tool_trace
      weight: 1.0
  failure_modes: [skipped_required_verification]
---
## Instructions
Do triage.
""",
        )
        registry = SkillRegistry()
        registry.load_directory(tmp_path)
        record = registry.records()[0]
        assert record.execution_contract["criteria"] == ["goal_completion"]
        assert record.execution_contract["observable_signals"] == ["tool_verification_used"]

    def test_find_matches_reports_reasons(self, registry: SkillRegistry):
        matches = registry.find_matches("write a long-form article")
        assert matches
        assert matches[0].skill.name == "write-article"
        assert matches[0].reasons

    def test_find_similar_detects_duplicate_skill(self, registry: SkillRegistry):
        matches = registry.find_similar(
            name="write-article",
            text="Create a reusable writing workflow",
        )
        assert matches
        assert matches[0].skill.name == "write-article"
        assert "exact_name" in matches[0].reasons


class TestRegistryTriggerMatching:
    @pytest.fixture
    def registry(self, tmp_path: Path) -> SkillRegistry:
        _write_skill(tmp_path / "anthropic" / "SKILL.md", CLAUDE_STYLE_SKILL)
        _write_skill(tmp_path / "writing" / "SKILL.md", SAMPLE_SKILL)
        reg = SkillRegistry()
        reg.load_directory(tmp_path)
        return reg

    def test_trigger_match_boosts_score(self, registry: SkillRegistry):
        results = registry.find_relevant("how do I add prompt caching to my code?")
        assert results
        assert results[0].name == "api-helper"

    def test_anti_trigger_excludes(self, registry: SkillRegistry):
        # Mentions openai which is in anti_triggers — should exclude api-helper
        results = registry.find_relevant("user imports openai sdk for chat")
        assert not any(s.name == "api-helper" for s in results)

    def test_get_reference_loads_doc(self, tmp_path: Path):
        skill_dir = tmp_path / "skill-with-refs"
        _write_skill(skill_dir / "SKILL.md", SAMPLE_SKILL)
        _write_skill(skill_dir / "references" / "deep.md", "# Deep dive\nDetails")
        reg = SkillRegistry()
        reg.load_directory(tmp_path)
        ref = reg.get_reference("write-article", "deep.md")
        assert ref is not None
        assert "Details" in ref

    def test_get_reference_missing(self, registry: SkillRegistry):
        assert registry.get_reference("write-article", "nonexistent.md") is None
        assert registry.get_reference("nonexistent-skill", "anything.md") is None
