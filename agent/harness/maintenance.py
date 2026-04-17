"""Continuous quality sweep for agent-generated repository drift."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skills.loader import parse_skill


@dataclass
class SweepIssue:
    category: str
    severity: str
    title: str
    detail: str
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "path": self.path,
        }


class MaintenanceSweepRunner:
    """Produce structured sweep issues and targeted follow-up tasks."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(args),
            cwd=str(self.base_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )

    def sweep(self) -> dict[str, Any]:
        issues: list[SweepIssue] = []
        issues.extend(self._docs_issues())
        issues.extend(self._constraint_issues())
        issues.extend(self._skill_issues())
        issues.extend(self._docs_graph_issues())
        return {
            "issues": [issue.to_dict() for issue in issues],
            "suggested_tasks": [self._task_for(issue) for issue in issues[:20]],
        }

    def _docs_issues(self) -> list[SweepIssue]:
        result = self._run(sys.executable, "-m", "core.docs_lint", str(self.base_dir))
        if result.returncode == 0:
            return []
        issues = []
        for line in (result.stdout + result.stderr).splitlines():
            if line.startswith("ERROR: "):
                issues.append(
                    SweepIssue(
                        category="docs",
                        severity="high",
                        title="Documentation lint failure",
                        detail=line.removeprefix("ERROR: "),
                    )
                )
        return issues

    def _constraint_issues(self) -> list[SweepIssue]:
        result = self._run(sys.executable, "-m", "pytest", "tests/test_repo_constraints.py", "-q")
        if result.returncode == 0:
            return []
        summary = "\n".join(
            line for line in result.stdout.splitlines() if "FAILED" in line or "E " in line
        )
        return [
            SweepIssue(
                category="constraints",
                severity="high",
                title="Repository constraints failing",
                detail=summary or result.stdout.strip() or result.stderr.strip(),
            )
        ]

    def _skill_issues(self) -> list[SweepIssue]:
        issues: list[SweepIssue] = []
        skills_root = self.base_dir / "skills"
        if not skills_root.is_dir():
            return issues
        for path in skills_root.rglob("SKILL.md"):
            try:
                parse_skill(path)
            except Exception as exc:
                issues.append(
                    SweepIssue(
                        category="skills",
                        severity="medium",
                        title="Skill parse failure",
                        detail=str(exc),
                        path=str(path.relative_to(self.base_dir)),
                    )
                )
        return issues

    def _docs_graph_issues(self) -> list[SweepIssue]:
        docs_root = self.base_dir / "docs"
        if not docs_root.is_dir():
            return []
        referenced: set[Path] = set()
        markdown_files = list(docs_root.rglob("*.md"))
        for path in markdown_files:
            text = path.read_text(encoding="utf-8")
            for raw_target in [chunk.split(")", 1)[0] for chunk in text.split("](")[1:]]:
                target = raw_target.strip("<>").split("#", 1)[0]
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                candidate = (path.parent / target).resolve()
                if candidate.is_file():
                    referenced.add(candidate)
        issues: list[SweepIssue] = []
        for path in markdown_files:
            if path.name == "index.md":
                continue
            if path not in referenced:
                issues.append(
                    SweepIssue(
                        category="docs",
                        severity="low",
                        title="Unreferenced documentation file",
                        detail="No in-repo markdown link points to this file.",
                        path=str(path.relative_to(self.base_dir)),
                    )
                )
        return issues

    @staticmethod
    def _task_for(issue: SweepIssue) -> dict[str, Any]:
        return {
            "title": issue.title,
            "priority": issue.severity,
            "prompt": f"Resolve {issue.category} issue: {issue.detail}",
            "path": issue.path,
        }
