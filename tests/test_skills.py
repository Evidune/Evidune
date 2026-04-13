"""Tests for skills/loader.py and skills/registry.py."""

from pathlib import Path

import pytest

from skills.loader import parse_skill, load_skills_from_dir
from skills.registry import SkillRegistry


def _write_skill(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


SAMPLE_SKILL = """---
name: write-article
description: Write a compelling article for Zhihu
tags: [writing, zhihu, content]
outcome_metrics: true
---

## Instructions

Write an article that follows these guidelines:
- Be practical and concrete
- Use real examples
"""

BARE_SKILL = """# No Frontmatter Skill

Just instructions, no YAML header.
"""


class TestParseSkill:
    def test_full_skill(self, tmp_path: Path):
        path = _write_skill(tmp_path / "SKILL.md", SAMPLE_SKILL)
        skill = parse_skill(path)
        assert skill.name == "write-article"
        assert skill.description == "Write a compelling article for Zhihu"
        assert "writing" in skill.tags
        assert skill.outcome_metrics is True
        assert "Be practical" in skill.instructions

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
        _write_skill(tmp_path / "review" / "SKILL.md",
                      "---\nname: review-data\ndescription: Review analytics data\ntags: [analytics, review]\n---\nReview instructions")
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
        results = registry.find_relevant("write an article for zhihu")
        assert len(results) > 0
        assert results[0].name == "write-article"

    def test_find_relevant_by_tag(self, registry: SkillRegistry):
        results = registry.find_relevant("analytics review")
        assert any(s.name == "review-data" for s in results)

    def test_as_system_prompt(self, registry: SkillRegistry):
        prompt = registry.as_system_prompt()
        assert "write-article" in prompt
        assert "review-data" in prompt
        assert "# Available Skills" in prompt
