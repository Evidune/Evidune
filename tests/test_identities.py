"""Tests for identities/loader.py and identities/registry.py."""

from pathlib import Path

import pytest

from identities.loader import Identity, load_identities_from_dir, parse_identity
from identities.registry import IdentityRegistry


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


IDENTITY_META = """---
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
"""

IDENTITY_MINIMAL = """---
name: minimal
---

just the identity body
"""

SOUL_SAMPLE = """## 风格

实在话、有钩子、不水。
"""

USER_SAMPLE = """## 关系

你把用户当同行。
"""

TOOLS_SAMPLE = """## 工具偏好

先读上下文，再动手。
"""


def _write_identity(
    root: Path,
    *,
    identity_md: str = IDENTITY_META,
    soul_md: str = SOUL_SAMPLE,
    user_md: str = USER_SAMPLE,
    tools_md: str | None = None,
) -> Path:
    _write(root / "SOUL.md", soul_md)
    _write(root / "IDENTITY.md", identity_md)
    _write(root / "USER.md", user_md)
    if tools_md is not None:
        _write(root / "TOOLS.md", tools_md)
    return root


class TestParseIdentity:
    def test_full_identity(self, tmp_path: Path):
        root = _write_identity(tmp_path / "zhihu-writer", tools_md=TOOLS_SAMPLE)
        identity = parse_identity(root)
        assert identity.name == "zhihu-writer"
        assert identity.display_name == "老拐"
        assert identity.description == "资深内容创作者"
        assert identity.language == "zh-CN"
        assert "zhihu" in identity.expertise
        assert identity.voice == "实在、不端着"
        assert identity.default is True
        assert "实在话" in identity.soul
        assert "你叫老拐" in identity.identity
        assert "你把用户当同行" in identity.user
        assert "先读上下文" in identity.tools
        assert identity.path == root

    def test_minimal_identity_uses_directory_name_as_display_name(self, tmp_path: Path):
        root = _write_identity(tmp_path / "minimal", identity_md=IDENTITY_MINIMAL)
        identity = parse_identity(root)
        assert identity.name == "minimal"
        assert identity.display_name == "minimal"
        assert identity.identity == "just the identity body"
        assert identity.default is False

    def test_extra_meta_preserved(self, tmp_path: Path):
        root = _write_identity(
            tmp_path / "x",
            identity_md="---\nname: x\nweird_field: hello\n---\n\nbody",
        )
        identity = parse_identity(root)
        assert identity.meta.get("weird_field") == "hello"

    def test_missing_required_file_raises(self, tmp_path: Path):
        root = tmp_path / "broken"
        _write(root / "SOUL.md", SOUL_SAMPLE)
        _write(root / "IDENTITY.md", IDENTITY_META)
        with pytest.raises(ValueError, match="USER.md"):
            parse_identity(root)

    def test_namespace_property(self):
        identity = Identity(name="alice", path=Path("/x"))
        assert identity.namespace == "identity:alice"

    def test_prompt_renders_labeled_sections(self):
        identity = Identity(
            name="alice",
            soul="soul block",
            identity="identity block",
            user="user block",
            tools="tools block",
            path=Path("/x"),
        )
        prompt = identity.prompt
        assert "# SOUL" in prompt
        assert "# IDENTITY" in prompt
        assert "# USER" in prompt
        assert "# TOOLS" in prompt


class TestLoadIdentitiesFromDir:
    def test_loads_subdirectory_identity_packages(self, tmp_path: Path):
        _write_identity(tmp_path / "alice")
        _write_identity(tmp_path / "bob", identity_md=IDENTITY_MINIMAL)
        identities = load_identities_from_dir(tmp_path)
        assert len(identities) == 2

    def test_empty_dir(self, tmp_path: Path):
        assert load_identities_from_dir(tmp_path) == []

    def test_nonexistent_dir(self):
        assert load_identities_from_dir("/nonexistent") == []

    def test_direct_identity_directory(self, tmp_path: Path):
        _write_identity(tmp_path, identity_md=IDENTITY_META)
        identities = load_identities_from_dir(tmp_path)
        assert len(identities) == 1
        assert identities[0].name == "zhihu-writer"


class TestIdentityRegistry:
    @pytest.fixture
    def registry(self, tmp_path: Path) -> IdentityRegistry:
        _write_identity(tmp_path / "writer")
        _write_identity(tmp_path / "minimal", identity_md=IDENTITY_MINIMAL)
        registry = IdentityRegistry()
        registry.load_directory(tmp_path)
        return registry

    def test_loaded_count(self, registry: IdentityRegistry):
        assert len(registry) == 2

    def test_get_by_name(self, registry: IdentityRegistry):
        identity = registry.get("zhihu-writer")
        assert identity is not None
        assert identity.display_name == "老拐"

    def test_default_picks_marked_identity(self, registry: IdentityRegistry):
        default = registry.default()
        assert default is not None
        assert default.name == "zhihu-writer"

    def test_default_falls_back_to_first(self, tmp_path: Path):
        _write_identity(tmp_path / "p1", identity_md=IDENTITY_MINIMAL)
        registry = IdentityRegistry()
        registry.load_directory(tmp_path)
        assert registry.default() is not None
        assert registry.default().name == "minimal"

    def test_resolve_with_name(self, registry: IdentityRegistry):
        identity = registry.resolve("minimal")
        assert identity is not None
        assert identity.name == "minimal"

    def test_resolve_unknown_name_returns_none(self, registry: IdentityRegistry):
        assert registry.resolve("nonexistent") is None

    def test_resolve_no_name_returns_default(self, registry: IdentityRegistry):
        identity = registry.resolve(None)
        assert identity is not None
        assert identity.name == "zhihu-writer"

    def test_set_default_explicit(self, registry: IdentityRegistry):
        registry.set_default("minimal")
        assert registry.default().name == "minimal"

    def test_set_default_unknown_raises(self, registry: IdentityRegistry):
        with pytest.raises(KeyError):
            registry.set_default("nonexistent")

    def test_register_direct(self):
        registry = IdentityRegistry()
        identity = Identity(name="direct", path=Path("/x"))
        registry.register(identity)
        assert registry.get("direct") is identity

    def test_empty_registry(self):
        registry = IdentityRegistry()
        assert registry.default() is None
        assert registry.resolve(None) is None
        assert len(registry) == 0
