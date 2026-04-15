"""External tools: shell / file / http / python / grep / glob.

Full agent capability with scale-based constraints (not sandboxing):
- shell: timeout, cwd pinned to base_dir, output truncated at max bytes
- file read/write: paths resolved under base_dir (symlink-safe)
- http_get: timeout + response size cap
- execute_python: subprocess with timeout + stdout cap
- grep / glob: rooted at base_dir, result count cap

For 'full trust in user env' mode — no sandbox. Suitable for local
dev but NOT for hosting untrusted agents.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.tools.base import Tool


@dataclass
class ExternalToolsConfig:
    shell_timeout_s: int = 60
    shell_output_bytes: int = 20_000
    file_read_max_bytes: int = 200_000
    file_write_max_bytes: int = 500_000
    http_timeout_s: int = 30
    http_max_bytes: int = 500_000
    python_timeout_s: int = 30
    python_output_bytes: int = 20_000
    grep_max_hits: int = 200
    glob_max_hits: int = 200


def _resolve_under(base: Path, path: str) -> Path:
    """Resolve `path` under `base`, raising ValueError if it escapes."""
    base = base.resolve()
    candidate = (base / path).resolve() if not os.path.isabs(path) else Path(path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as e:
        raise ValueError(f"Path {path!r} escapes base {str(base)!r}") from e
    return candidate


def external_tools(base_dir: Path, config: ExternalToolsConfig | Any = None) -> list[Tool]:
    """Build the full external tool set rooted at `base_dir`."""
    cfg = config if isinstance(config, ExternalToolsConfig) else ExternalToolsConfig()
    base = Path(base_dir).resolve()

    # --- shell ---

    async def run_shell(command: str, cwd: str = "") -> str:
        working = _resolve_under(base, cwd) if cwd else base
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(working),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=cfg.shell_timeout_s)
        except TimeoutError:
            proc.kill()
            return f"(command timed out after {cfg.shell_timeout_s}s)"
        body = (stdout or b"").decode(errors="replace")
        if len(body) > cfg.shell_output_bytes:
            body = body[: cfg.shell_output_bytes] + "\n…(truncated)"
        return f"exit_code={proc.returncode}\n{body}"

    # --- file read / write / edit ---

    async def read_file(path: str) -> str:
        full = _resolve_under(base, path)
        if not full.is_file():
            return f"(not a file: {path})"
        try:
            content = full.read_bytes()
        except OSError as e:
            return f"(read error: {e})"
        if len(content) > cfg.file_read_max_bytes:
            content = content[: cfg.file_read_max_bytes]
            return content.decode(errors="replace") + "\n…(truncated)"
        return content.decode(errors="replace")

    async def write_file(path: str, content: str) -> str:
        if len(content.encode("utf-8")) > cfg.file_write_max_bytes:
            return f"(content exceeds {cfg.file_write_max_bytes}-byte limit)"
        full = _resolve_under(base, path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars to {full.relative_to(base)}"

    async def edit_file(path: str, old: str, new: str) -> str:
        full = _resolve_under(base, path)
        if not full.is_file():
            return f"(not a file: {path})"
        text = full.read_text(encoding="utf-8")
        if old not in text:
            return f"(old string not found in {path})"
        updated = text.replace(old, new, 1)
        full.write_text(updated, encoding="utf-8")
        return f"Edited {full.relative_to(base)}"

    # --- http ---

    async def http_get(url: str) -> str:
        import httpx

        async with httpx.AsyncClient(timeout=cfg.http_timeout_s, follow_redirects=True) as c:
            resp = await c.get(url)
            body = resp.content[: cfg.http_max_bytes]
            text = body.decode(errors="replace")
            if len(resp.content) > cfg.http_max_bytes:
                text += "\n…(truncated)"
            return f"status={resp.status_code}\n{text}"

    # --- python ---

    async def execute_python(code: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "python3",
            "-c",
            code,
            cwd=str(base),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=cfg.python_timeout_s)
        except TimeoutError:
            proc.kill()
            return f"(python timed out after {cfg.python_timeout_s}s)"
        body = (stdout or b"").decode(errors="replace")
        if len(body) > cfg.python_output_bytes:
            body = body[: cfg.python_output_bytes] + "\n…(truncated)"
        return f"exit_code={proc.returncode}\n{body}"

    # --- grep / glob ---

    async def grep(pattern: str, path: str = "", file_glob: str = "*") -> list[dict]:
        root = _resolve_under(base, path) if path else base
        if not root.exists():
            return [{"error": f"not found: {path}"}]
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return [{"error": f"bad regex: {e}"}]

        hits: list[dict] = []
        scan_roots = [root] if root.is_dir() else [root.parent]
        for r in scan_roots:
            for p in r.rglob("*") if r.is_dir() else [r]:
                if len(hits) >= cfg.grep_max_hits:
                    break
                if not p.is_file():
                    continue
                if not fnmatch.fnmatch(p.name, file_glob):
                    continue
                try:
                    for i, line in enumerate(
                        p.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                    ):
                        if regex.search(line):
                            hits.append(
                                {
                                    "file": str(p.relative_to(base)),
                                    "line": i,
                                    "text": line[:300],
                                }
                            )
                            if len(hits) >= cfg.grep_max_hits:
                                break
                except (OSError, UnicodeDecodeError):
                    continue
        return hits

    async def glob(pattern: str, path: str = "") -> list[str]:
        root = _resolve_under(base, path) if path else base
        if not root.is_dir():
            return []
        matches = []
        for p in root.rglob(pattern):
            if len(matches) >= cfg.glob_max_hits:
                break
            matches.append(str(p.relative_to(base)))
        return matches

    return [
        Tool(
            name="run_shell",
            description=(
                "Run a shell command in the project directory. "
                "Times out after ~60s; stdout+stderr combined and truncated."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {
                        "type": "string",
                        "description": "Optional sub-path relative to project root",
                    },
                },
                "required": ["command"],
            },
            handler=run_shell,
        ),
        Tool(
            name="read_file",
            description="Read a text file's contents (truncated if large).",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            handler=read_file,
        ),
        Tool(
            name="write_file",
            description="Write content to a file, creating parent dirs as needed.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            handler=write_file,
        ),
        Tool(
            name="edit_file",
            description=(
                "Find-and-replace a substring in a file (first occurrence). "
                "Use for small targeted edits; use write_file for rewrites."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                },
                "required": ["path", "old", "new"],
            },
            handler=edit_file,
        ),
        Tool(
            name="http_get",
            description="Fetch a URL via HTTP GET (follows redirects; truncates large bodies).",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            handler=http_get,
        ),
        Tool(
            name="execute_python",
            description=(
                "Run a short Python snippet via `python3 -c`. "
                "Returns combined stdout+stderr + exit code."
            ),
            parameters={
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
            handler=execute_python,
        ),
        Tool(
            name="grep",
            description="Regex-search file contents under the project root.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Subdir to search (optional)"},
                    "file_glob": {
                        "type": "string",
                        "description": "Filename glob (default '*')",
                    },
                },
                "required": ["pattern"],
            },
            handler=grep,
        ),
        Tool(
            name="glob",
            description="List file paths matching a glob pattern under the project root.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Subdir to search (optional)"},
                },
                "required": ["pattern"],
            },
            handler=glob,
        ),
    ]


# shlex import is used in docstrings/comments mental model only; keep import
# available for future use
_ = shlex
