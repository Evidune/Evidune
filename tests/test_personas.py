"""Tests for personas/loader.py and personas/registry.py."""

from pathlib import Path

import pytest

from personas.loader import Persona, load_personas_from_dir, parse_persona
from personas.registry import PersonaRegistry


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


SAMPLE_PERSONA = """---
name: zhihu-writer
display_name: 老拐
description: 资深内容创作者
language: zh-CN
expertise: [zhihu, writing]
voice: 实在、不端着
default: true
---

## 你是谁

你叫老拐，写知乎超过 5 年。

## 风格

实在话、有钩子、不水。
"""

MINIMAL_PERSONA = """---
name: minimal
---
just a body
"""


class TestParsePersona:
    def test_full_persona(self, tmp_path: Path):
        path = _write(tmp_path / "PERSONA.md", SAMPLE_PERSONA)
        p = parse_persona(path)
        assert p.name == "zhihu-writer"
        assert p.display_name == "老拐"
        assert p.description == "资深内容创作者"
        assert p.language == "zh-CN"
        assert "zhihu" in p.expertise
        assert p.voice == "实在、不端着"
        assert p.default is True
        assert "你叫老拐" in p.body
        assert p.path == path

    def test_minimal_persona(self, tmp_path: Path):
        path = _write(tmp_path / "PERSONA.md", MINIMAL_PERSONA)
        p = parse_persona(path)
        assert p.name == "minimal"
        assert p.display_name == "minimal"
        assert p.body == "just a body"
        assert p.default is False

    def test_no_frontmatter_uses_filename(self, tmp_path: Path):
        path = _write(tmp_path / "PERSONA.md", "no frontmatter at all")
        p = parse_persona(path)
        assert p.name == "PERSONA"
        assert p.body == "no frontmatter at all"

    def test_extra_meta_preserved(self, tmp_path: Path):
        content = "---\nname: x\nweird_field: hello\n---\n\nbody"
        path = _write(tmp_path / "PERSONA.md", content)
        p = parse_persona(path)
        assert p.meta.get("weird_field") == "hello"

    def test_namespace_property(self):
        p = Persona(name="alice", body="...", path=Path("/x/PERSONA.md"))
        assert p.namespace == "persona:alice"


class TestLoadPersonasFromDir:
    def test_loads_subdirectory_personas(self, tmp_path: Path):
        _write(tmp_path / "alice" / "PERSONA.md", SAMPLE_PERSONA)
        _write(tmp_path / "bob" / "PERSONA.md", MINIMAL_PERSONA)
        personas = load_personas_from_dir(tmp_path)
        assert len(personas) == 2

    def test_empty_dir(self, tmp_path: Path):
        assert load_personas_from_dir(tmp_path) == []

    def test_nonexistent_dir(self):
        assert load_personas_from_dir("/nonexistent") == []

    def test_inline_persona_md(self, tmp_path: Path):
        _write(tmp_path / "PERSONA.md", SAMPLE_PERSONA)
        personas = load_personas_from_dir(tmp_path)
        assert len(personas) == 1


class TestPersonaRegistry:
    @pytest.fixture
    def registry(self, tmp_path: Path) -> PersonaRegistry:
        _write(tmp_path / "writer" / "PERSONA.md", SAMPLE_PERSONA)
        _write(tmp_path / "minimal" / "PERSONA.md", MINIMAL_PERSONA)
        reg = PersonaRegistry()
        reg.load_directory(tmp_path)
        return reg

    def test_loaded_count(self, registry: PersonaRegistry):
        assert len(registry) == 2

    def test_get_by_name(self, registry: PersonaRegistry):
        p = registry.get("zhihu-writer")
        assert p is not None
        assert p.display_name == "老拐"

    def test_default_picks_marked_persona(self, registry: PersonaRegistry):
        d = registry.default()
        assert d is not None
        assert d.name == "zhihu-writer"  # has default: true

    def test_default_falls_back_to_first(self, tmp_path: Path):
        _write(tmp_path / "p1" / "PERSONA.md", MINIMAL_PERSONA)
        reg = PersonaRegistry()
        reg.load_directory(tmp_path)
        assert reg.default() is not None
        assert reg.default().name == "minimal"

    def test_resolve_with_name(self, registry: PersonaRegistry):
        p = registry.resolve("minimal")
        assert p is not None
        assert p.name == "minimal"

    def test_resolve_unknown_name_returns_none(self, registry: PersonaRegistry):
        assert registry.resolve("nonexistent") is None

    def test_resolve_no_name_returns_default(self, registry: PersonaRegistry):
        p = registry.resolve(None)
        assert p is not None
        assert p.name == "zhihu-writer"

    def test_set_default_explicit(self, registry: PersonaRegistry):
        registry.set_default("minimal")
        assert registry.default().name == "minimal"

    def test_set_default_unknown_raises(self, registry: PersonaRegistry):
        with pytest.raises(KeyError):
            registry.set_default("nonexistent")

    def test_register_direct(self):
        reg = PersonaRegistry()
        p = Persona(name="direct", body="hi", path=Path("/x"))
        reg.register(p)
        assert reg.get("direct") is p

    def test_empty_registry(self):
        reg = PersonaRegistry()
        assert reg.default() is None
        assert reg.resolve(None) is None
        assert len(reg) == 0
