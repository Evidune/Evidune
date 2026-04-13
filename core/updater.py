"""Markdown reference document updater with safe patching strategies."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class UpdateResult:
    path: str
    strategy: str
    has_changes: bool
    old_content: str
    new_content: str


def _find_section(content: str, heading: str) -> tuple[int, int] | None:
    """Find start and end positions of a markdown section by heading text.

    Returns (start, end) where start is the beginning of the heading line
    and end is the beginning of the next same-or-higher-level heading (or EOF).
    """
    lines = content.split("\n")
    # Determine heading level from the heading text
    heading_stripped = heading.lstrip("#").strip()
    start_idx = None
    start_level = None

    for i, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+)", line)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            if title == heading_stripped and start_idx is None:
                start_idx = i
                start_level = level
            elif start_idx is not None and level <= start_level:
                # Found the next same-or-higher-level heading
                start_pos = sum(len(lines[j]) + 1 for j in range(start_idx))
                end_pos = sum(len(lines[j]) + 1 for j in range(i))
                return start_pos, end_pos

    if start_idx is not None:
        start_pos = sum(len(lines[j]) + 1 for j in range(start_idx))
        return start_pos, len(content)

    return None


def append_only(current: str, new_content: str) -> str:
    """Append new content to the end of the document.

    Only appends lines that don't already exist in the document.
    """
    existing_lines = set(current.strip().split("\n"))
    new_lines = new_content.strip().split("\n")
    to_append = [line for line in new_lines if line.strip() and line not in existing_lines]

    if not to_append:
        return current

    result = current.rstrip()
    if result:
        result += "\n\n"
    result += "\n".join(to_append) + "\n"
    return result


def replace_section(current: str, section_heading: str, new_section_content: str) -> str:
    """Replace a specific markdown section, keeping everything else intact."""
    bounds = _find_section(current, section_heading)

    if bounds is None:
        # Section doesn't exist, append it
        result = current.rstrip()
        if result:
            result += "\n\n"
        result += new_section_content.rstrip() + "\n"
        return result

    start, end = bounds
    before = current[:start]
    after = current[end:]

    result = before + new_section_content.rstrip() + "\n"
    if after.strip():
        result += "\n" + after.lstrip("\n")

    return result


def full_replace(current: str, new_content: str) -> str:
    """Fully replace the document content."""
    return new_content


def update_reference(
    path: str | Path,
    strategy: str,
    new_content: str,
    section: str | None = None,
) -> UpdateResult:
    """Update a reference document using the specified strategy.

    Args:
        path: Path to the markdown file.
        strategy: One of 'append_only', 'replace_section', 'full_replace'.
        new_content: The new content to apply.
        section: Required for 'replace_section' — the heading to replace.

    Returns:
        UpdateResult with the old and new content and whether changes occurred.
    """
    path = Path(path)
    if path.exists():
        old_content = path.read_text(encoding="utf-8")
    else:
        old_content = ""

    if strategy == "append_only":
        result = append_only(old_content, new_content)
    elif strategy == "replace_section":
        if not section:
            raise ValueError("replace_section requires a section heading")
        result = replace_section(old_content, section, new_content)
    elif strategy == "full_replace":
        result = full_replace(old_content, new_content)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")

    has_changes = result != old_content

    if has_changes:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result, encoding="utf-8")

    return UpdateResult(
        path=str(path),
        strategy=strategy,
        has_changes=has_changes,
        old_content=old_content,
        new_content=result,
    )
