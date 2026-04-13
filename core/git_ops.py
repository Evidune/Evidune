"""Git operations for iteration audit trail."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class CommitResult:
    success: bool
    message: str
    sha: str | None = None
    error: str | None = None


def _run_git(args: list[str], cwd: str | Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
    )


def is_git_repo(path: str | Path) -> bool:
    result = _run_git(["rev-parse", "--is-inside-work-tree"], path)
    return result.returncode == 0


def has_changes(path: str | Path) -> bool:
    result = _run_git(["status", "--porcelain"], path)
    return bool(result.stdout.strip())


def commit_changes(
    repo_path: str | Path,
    changed_files: list[str],
    prefix: str = "chore(review): ",
    summary: str = "",
) -> CommitResult:
    """Stage and commit changed files with a structured message.

    Args:
        repo_path: Path to the git repository root.
        changed_files: List of file paths (relative to repo) to stage.
        prefix: Commit message prefix.
        summary: Short summary of what changed.

    Returns:
        CommitResult indicating success or failure.
    """
    repo_path = Path(repo_path)

    if not is_git_repo(repo_path):
        return CommitResult(success=False, message="", error="Not a git repository")

    # Stage specific files
    for f in changed_files:
        result = _run_git(["add", f], repo_path)
        if result.returncode != 0:
            return CommitResult(
                success=False,
                message="",
                error=f"Failed to stage {f}: {result.stderr}",
            )

    # Check if there's anything staged
    result = _run_git(["diff", "--cached", "--quiet"], repo_path)
    if result.returncode == 0:
        return CommitResult(success=True, message="No changes to commit", sha=None)

    # Build commit message
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    message = f"{prefix}{now}"
    if summary:
        message += f" — {summary}"

    result = _run_git(["commit", "-m", message], repo_path)
    if result.returncode != 0:
        return CommitResult(success=False, message=message, error=result.stderr)

    # Get the commit SHA
    sha_result = _run_git(["rev-parse", "HEAD"], repo_path)
    sha = sha_result.stdout.strip() if sha_result.returncode == 0 else None

    return CommitResult(success=True, message=message, sha=sha)
