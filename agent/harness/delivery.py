"""Structured delivery pipeline for harness-managed changes."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.harness.runtime import RuntimeEnvironment


@dataclass
class DeliveryConfig:
    branch_prefix: str = "codex/"
    github_enabled: bool = True
    auto_stage_tracked: bool = True
    ci_poll_interval_s: int = 5
    ci_timeout_s: int = 120


class DeliveryManager:
    """Git-first delivery pipeline with GitHub fallback when available."""

    def __init__(self, base_dir: Path, config: DeliveryConfig | None = None) -> None:
        self.base_dir = base_dir
        self.config = config or DeliveryConfig()

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(args),
            cwd=str(self.base_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )

    def _state_path(self, environment: RuntimeEnvironment) -> Path:
        target = environment.root / "delivery" / "state.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _read_state(self, environment: RuntimeEnvironment) -> dict[str, Any]:
        path = self._state_path(environment)
        if not path.is_file():
            return {"review_comments": []}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"review_comments": []}

    def _write_state(self, environment: RuntimeEnvironment, payload: dict[str, Any]) -> None:
        self._state_path(environment).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def submit(
        self,
        environment: RuntimeEnvironment,
        *,
        files: list[str] | None = None,
        branch: str = "",
        message: str = "",
        pr_title: str = "",
        pr_body: str = "",
    ) -> dict[str, Any]:
        if self._run("git", "rev-parse", "--is-inside-work-tree").returncode != 0:
            raise RuntimeError("Delivery pipeline requires a git repository")

        branch_name = branch or f"{self.config.branch_prefix}{environment.task_id.split('-')[-1]}"
        current_branch = self._run("git", "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if current_branch != branch_name:
            checkout = self._run("git", "checkout", "-B", branch_name)
            if checkout.returncode != 0:
                raise RuntimeError(checkout.stderr.strip() or "Failed to create delivery branch")

        stage_targets = files or []
        if not stage_targets and self.config.auto_stage_tracked:
            stage = self._run("git", "add", "-u")
        else:
            stage = self._run("git", "add", *stage_targets)
        if stage.returncode != 0:
            raise RuntimeError(stage.stderr.strip() or "Failed to stage files")

        diff = self._run("git", "diff", "--cached", "--name-only")
        changed_files = [line for line in diff.stdout.splitlines() if line.strip()]
        commit_sha = ""
        commit_message = message or f"chore(harness): deliver {environment.task_id}"
        if changed_files:
            commit = self._run("git", "commit", "-m", commit_message)
            if commit.returncode == 0:
                commit_sha = self._run("git", "rev-parse", "HEAD").stdout.strip()
        pr = self._create_pull_request(branch_name, pr_title, pr_body)
        ci = self._poll_ci(pr.get("number", "")) if pr.get("created") else {"status": "skipped"}
        payload = {
            "mode": pr.get("mode", "local"),
            "branch": branch_name,
            "commit_sha": commit_sha,
            "changed_files": changed_files,
            "pull_request": pr,
            "ci": ci,
            "review_comments": self._read_state(environment).get("review_comments", []),
        }
        self._write_state(environment, payload)
        return payload

    def _create_pull_request(self, branch: str, title: str, body: str) -> dict[str, Any]:
        if not self.config.github_enabled or shutil.which("gh") is None:
            return {"created": False, "mode": "local"}
        repo = self._run("git", "remote", "get-url", "origin")
        if repo.returncode != 0 or "github.com" not in repo.stdout:
            return {"created": False, "mode": "local"}
        push = self._run("git", "push", "-u", "origin", branch)
        if push.returncode != 0:
            return {"created": False, "mode": "local", "error": push.stderr.strip()}
        pr_title = title or branch
        create = self._run(
            "gh",
            "pr",
            "create",
            "--fill",
            "--title",
            pr_title,
            "--body",
            body or pr_title,
        )
        if create.returncode != 0:
            return {"created": False, "mode": "local", "error": create.stderr.strip()}
        url = create.stdout.strip().splitlines()[-1]
        return {
            "created": True,
            "mode": "github",
            "url": url,
            "number": url.rstrip("/").split("/")[-1],
        }

    def _poll_ci(self, pr_number: str) -> dict[str, Any]:
        if not pr_number or shutil.which("gh") is None:
            return {"status": "skipped"}
        deadline = time.time() + self.config.ci_timeout_s
        latest: dict[str, Any] = {"status": "pending", "checks": []}
        while time.time() < deadline:
            result = self._run("gh", "pr", "checks", pr_number, "--json", "name,state,link")
            if result.returncode != 0:
                return {"status": "error", "error": result.stderr.strip()}
            checks = json.loads(result.stdout or "[]")
            states = {item.get("state", "").lower() for item in checks}
            latest = {"status": "pending", "checks": checks}
            if states and states <= {"success"}:
                latest["status"] = "passed"
                return latest
            if "failure" in states or "cancelled" in states:
                latest["status"] = "failed"
                return latest
            time.sleep(self.config.ci_poll_interval_s)
        latest["status"] = "timeout"
        return latest

    def list_review_comments(self, environment: RuntimeEnvironment) -> list[dict[str, Any]]:
        return self._read_state(environment).get("review_comments", [])

    def add_review_comment(
        self,
        environment: RuntimeEnvironment,
        *,
        body: str,
        author: str = "agent",
        path: str = "",
        line: int = 0,
    ) -> dict[str, Any]:
        state = self._read_state(environment)
        comments = state.setdefault("review_comments", [])
        comment = {
            "id": len(comments) + 1,
            "author": author,
            "body": body,
            "path": path,
            "line": line,
            "responded": False,
        }
        comments.append(comment)
        self._write_state(environment, state)
        return comment

    def respond_review_comment(
        self,
        environment: RuntimeEnvironment,
        *,
        comment_id: int,
        response: str,
    ) -> dict[str, Any]:
        state = self._read_state(environment)
        for comment in state.setdefault("review_comments", []):
            if int(comment.get("id", 0)) == int(comment_id):
                comment["responded"] = True
                comment["response"] = response
                self._write_state(environment, state)
                return comment
        raise ValueError(f"Unknown review comment id: {comment_id}")
