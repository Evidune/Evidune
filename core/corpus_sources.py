"""Reproducible, commit-pinned source checkout for evaluation corpora."""

from __future__ import annotations

import hashlib
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adapters.benchmark import CorpusSource, EvaluationCorpus, source_checkout_path, write_json


def _git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ValueError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sync_git_source(source: CorpusSource, source_root: Path) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", source.commit):
        raise ValueError(f"git source {source.name} requires a full commit SHA")
    if source.url.startswith("-"):
        raise ValueError(f"git source {source.name} has an unsafe URL")
    target = source_checkout_path(source, source_root)
    if target.exists():
        if not (target / ".git").is_dir():
            raise ValueError(f"managed source path is not a git checkout: {target}")
        if _git("status", "--porcelain", cwd=target):
            raise ValueError(f"managed source checkout has local changes: {target}")
        current_url = _git("remote", "get-url", "origin", cwd=target)
        if current_url != source.url:
            raise ValueError(
                f"source URL mismatch for {source.name}: {current_url} != {source.url}"
            )
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--filter=blob:none", "--no-checkout", "--", source.url, str(target))
    try:
        _git("cat-file", "-e", f"{source.commit}^{{commit}}", cwd=target)
    except ValueError:
        _git("fetch", "--depth", "1", "origin", source.commit, cwd=target)
    _git("checkout", "--detach", source.commit, cwd=target)
    resolved_commit = _git("rev-parse", "HEAD", cwd=target)
    if resolved_commit != source.commit.lower():
        raise ValueError(
            f"source commit mismatch for {source.name}: {resolved_commit} != {source.commit}"
        )
    files: dict[str, str] = {}
    for raw_path in source.paths:
        path = (target / raw_path).resolve()
        if not path.is_file() or not path.is_relative_to(target.resolve()):
            raise ValueError(f"pinned source path is missing or escapes checkout: {raw_path}")
        files[raw_path] = _file_digest(path)
    return {
        "name": source.name,
        "url": source.url,
        "commit": resolved_commit,
        "tree": _git("rev-parse", "HEAD^{tree}", cwd=target),
        "license": source.license,
        "checkout": str(target),
        "files": files,
    }


def sync_corpus_sources(corpus: EvaluationCorpus, source_root: Path) -> dict[str, Any]:
    """Fetch all git-mode sources and write an exact local lock artifact."""
    return sync_pinned_sources(
        snapshot_id=corpus.corpus_id,
        manifest_digest=corpus.manifest_digest,
        sources=corpus.sources,
        source_root=source_root,
    )


def sync_pinned_sources(
    *,
    snapshot_id: str,
    manifest_digest: str,
    sources: list[CorpusSource],
    source_root: Path,
) -> dict[str, Any]:
    """Fetch commit-pinned sources for a corpus or a Skill catalog."""
    locks = [_sync_git_source(source, source_root) for source in sources if source.mode == "git"]
    payload = {
        "snapshot_id": snapshot_id,
        "manifest_digest": manifest_digest,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "sources": locks,
    }
    write_json(source_root.parent / "source-lock.json", payload)
    return payload
