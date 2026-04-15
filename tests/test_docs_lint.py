"""Tests for repository docs lint rules."""

from pathlib import Path

from core.docs_lint import lint_repo

REQUIRED_DOC_PATHS = [
    "README.md",
    "tasks.md",
    "docs/index.md",
    "docs/architecture.md",
    "docs/quality-score.md",
    "docs/reliability.md",
    "docs/tech-debt.md",
    "docs/exec-plans/active/README.md",
    "docs/exec-plans/completed/README.md",
    "docs/product-specs/index.md",
    "docs/references/index.md",
    "docs/generated/README.md",
]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_repo(root: Path) -> None:
    _write(
        root / "AGENTS.md",
        "# Agents\n\nLinks: docs/index.md docs/architecture.md tasks.md\n",
    )
    _write(root / "README.md", "[Docs](docs/index.md)\n")
    _write(root / "tasks.md", "# Tasks\n")
    for rel in REQUIRED_DOC_PATHS:
        content = "# Doc\n"
        if rel == "docs/index.md":
            content = "# Docs\n[Architecture](architecture.md)\n"
        _write(root / rel, content)
    for mirror in ("CLAUDE.md", "GEMINI.md", ".cursorrules"):
        (root / mirror).symlink_to("AGENTS.md")


def test_lint_repo_passes_for_valid_structure(tmp_path: Path):
    _seed_repo(tmp_path)
    assert lint_repo(tmp_path) == []


def test_lint_repo_reports_missing_required_path(tmp_path: Path):
    _seed_repo(tmp_path)
    (tmp_path / "docs" / "quality-score.md").unlink()
    errors = lint_repo(tmp_path)
    assert any("docs/quality-score.md" in error for error in errors)


def test_lint_repo_reports_oversized_agents(tmp_path: Path):
    _seed_repo(tmp_path)
    content = "\n".join(f"line {i}" for i in range(120))
    _write(tmp_path / "AGENTS.md", content)
    errors = lint_repo(tmp_path)
    assert any("AGENTS.md has" in error for error in errors)


def test_lint_repo_reports_bad_mirror_copy(tmp_path: Path):
    _seed_repo(tmp_path)
    (tmp_path / "CLAUDE.md").unlink()
    _write(tmp_path / "CLAUDE.md", "different")
    errors = lint_repo(tmp_path)
    assert any("CLAUDE.md" in error for error in errors)


def test_lint_repo_reports_broken_markdown_link(tmp_path: Path):
    _seed_repo(tmp_path)
    _write(tmp_path / "docs" / "references" / "index.md", "[Broken](missing.md)\n")
    errors = lint_repo(tmp_path)
    assert any("broken link" in error for error in errors)
