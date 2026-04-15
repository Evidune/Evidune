"""Repository documentation lint checks."""

from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED_PATHS = [
    "AGENTS.md",
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
MIRROR_PATHS = ["CLAUDE.md", "GEMINI.md", ".cursorrules"]
MAX_AGENTS_LINES = 110
LINK_RE = re.compile(r"\[[^\]]+]\(([^)]+)\)")


def lint_repo(base_dir: str | Path | None = None) -> list[str]:
    """Return repo documentation lint errors for `base_dir`."""
    root = Path(base_dir or Path.cwd()).resolve()
    errors: list[str] = []

    errors.extend(_check_required_paths(root))
    errors.extend(_check_agents(root))
    errors.extend(_check_mirrors(root))
    errors.extend(_check_markdown_links(root))

    return errors


def _check_required_paths(root: Path) -> list[str]:
    errors = []
    for rel in REQUIRED_PATHS:
        if not (root / rel).exists():
            errors.append(f"Missing required path: {rel}")
    return errors


def _check_agents(root: Path) -> list[str]:
    agents_path = root / "AGENTS.md"
    if not agents_path.exists():
        return []

    content = agents_path.read_text(encoding="utf-8")
    errors = []
    line_count = len(content.splitlines())
    if line_count > MAX_AGENTS_LINES:
        errors.append(f"AGENTS.md has {line_count} lines; limit is {MAX_AGENTS_LINES}")

    required_snippets = ["docs/index.md", "docs/architecture.md", "tasks.md"]
    for snippet in required_snippets:
        if snippet not in content:
            errors.append(f"AGENTS.md must reference {snippet}")
    return errors


def _check_mirrors(root: Path) -> list[str]:
    errors = []
    agents_path = root / "AGENTS.md"
    if not agents_path.exists():
        return errors

    agents_text = agents_path.read_text(encoding="utf-8")
    for rel in MIRROR_PATHS:
        mirror = root / rel
        if not mirror.exists():
            errors.append(f"Missing mirror file: {rel}")
            continue

        if mirror.is_symlink():
            if mirror.resolve() != agents_path.resolve():
                errors.append(f"{rel} must symlink to AGENTS.md")
            continue

        if mirror.read_text(encoding="utf-8") != agents_text:
            errors.append(f"{rel} must be a symlink to AGENTS.md or an exact copy")
    return errors


def _check_markdown_links(root: Path) -> list[str]:
    errors = []
    markdown_files = [
        root / "README.md",
        root / "AGENTS.md",
        *sorted((root / "docs").rglob("*.md")),
    ]

    for path in markdown_files:
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        for raw_target in LINK_RE.findall(content):
            target = raw_target.strip()
            if target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]

            target_path = target.split("#", 1)[0]
            if not target_path:
                continue
            resolved = (path.parent / target_path).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                errors.append(f"{path.relative_to(root)} links outside repo: {raw_target}")
                continue
            if not resolved.exists():
                errors.append(f"{path.relative_to(root)} has broken link: {raw_target}")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    base_dir = Path(args[0]).resolve() if args else Path.cwd().resolve()
    errors = lint_repo(base_dir)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    print(f"Documentation lint passed for {base_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
