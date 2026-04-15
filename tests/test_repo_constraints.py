"""Structural checks that keep the repo from drifting further."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON_LIMIT = 300
FRONTEND_LIMIT = 250
OVERSIZED_SECTION_RE = re.compile(
    r"## Oversized Files\n\n(?P<body>.*?)(?:\n## |\Z)",
    re.DOTALL,
)
OVERSIZED_ITEM_RE = re.compile(r"- `([^`]+)` \(hard limit exception\)")
FORBIDDEN_IMPORTS = {
    "memory": {"agent", "gateway", "skills", "channels", "adapters", "identities", "core"},
    "skills": {"agent", "gateway", "memory", "channels", "adapters", "identities", "core"},
    "gateway": {"core", "skills", "memory", "channels", "adapters", "identities"},
    "identities": {"agent", "gateway", "memory", "skills", "channels", "adapters", "core"},
    "agent": {"core", "channels", "adapters"},
}


def _oversized_allowlist() -> set[str]:
    content = (ROOT / "docs" / "tech-debt.md").read_text(encoding="utf-8")
    match = OVERSIZED_SECTION_RE.search(content)
    assert match, "docs/tech-debt.md must include an '## Oversized Files' section"
    return set(OVERSIZED_ITEM_RE.findall(match.group("body")))


def _line_count(path: Path) -> int:
    return sum(1 for _ in path.open(encoding="utf-8"))


def _imports_for(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports


def test_no_untracked_oversized_files():
    allowlist = _oversized_allowlist()
    actual: set[str] = set()

    for directory in (
        "agent",
        "core",
        "gateway",
        "memory",
        "skills",
        "channels",
        "adapters",
        "identities",
    ):
        for path in (ROOT / directory).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if _line_count(path) > PYTHON_LIMIT:
                actual.add(rel)

    for path in (ROOT / "web" / "src").rglob("*"):
        if path.suffix not in {".svelte", ".ts"}:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if _line_count(path) > FRONTEND_LIMIT:
            actual.add(rel)

    assert actual == allowlist


def test_import_boundaries_hold():
    for package, forbidden in FORBIDDEN_IMPORTS.items():
        for path in (ROOT / package).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            imports = _imports_for(path)
            offenders = sorted(imports & forbidden)
            assert not offenders, f"{rel} imports forbidden packages: {', '.join(offenders)}"
