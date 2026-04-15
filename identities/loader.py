"""Identity loader — OpenClaw-style assistant identity bundles.

Each identity lives at `<dir>/<identity-name>/` and is composed from:

- `SOUL.md`      personality, values, and behavior rules
- `IDENTITY.md`  assistant self-concept and optional metadata frontmatter
- `USER.md`      user context / working relationship
- `TOOLS.md`     optional tool-use preferences

Only the multi-file identity package layout is supported. `PERSONA.md`
is intentionally not loaded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_REQUIRED_FILES = ("SOUL.md", "IDENTITY.md", "USER.md")


@dataclass
class Identity:
    """A loaded assistant identity package."""

    name: str
    description: str = ""
    display_name: str = ""
    language: str = ""
    expertise: list[str] = field(default_factory=list)
    voice: str = ""
    default: bool = False
    soul: str = ""
    identity: str = ""
    user: str = ""
    tools: str = ""
    path: Path = Path()
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def namespace(self) -> str:
        """Memory namespace for this identity's facts."""
        return f"identity:{self.name}"

    @property
    def prompt(self) -> str:
        """Render the identity package into labeled prompt sections."""
        sections = []
        if self.soul:
            sections.append(f"# SOUL\n\n{self.soul}")
        if self.identity:
            sections.append(f"# IDENTITY\n\n{self.identity}")
        if self.user:
            sections.append(f"# USER\n\n{self.user}")
        if self.tools:
            sections.append(f"# TOOLS\n\n{self.tools}")
        return "\n\n".join(sections)


def _split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content.strip()
    frontmatter = yaml.safe_load(match.group(1)) or {}
    body = content[match.end() :].strip()
    return frontmatter, body


def _has_identity_files(directory: Path) -> bool:
    return all((directory / filename).is_file() for filename in _REQUIRED_FILES)


def parse_identity(directory: str | Path) -> Identity:
    """Parse an identity directory into an Identity object."""
    directory = Path(directory)
    missing = [filename for filename in _REQUIRED_FILES if not (directory / filename).is_file()]
    if missing:
        names = ", ".join(missing)
        raise ValueError(f"{directory} is missing required identity files: {names}")

    soul = (directory / "SOUL.md").read_text(encoding="utf-8").strip()
    identity_frontmatter, identity_body = _split_frontmatter(
        (directory / "IDENTITY.md").read_text(encoding="utf-8")
    )
    user = (directory / "USER.md").read_text(encoding="utf-8").strip()
    tools_path = directory / "TOOLS.md"
    tools = tools_path.read_text(encoding="utf-8").strip() if tools_path.is_file() else ""

    known = {
        "name",
        "display_name",
        "description",
        "language",
        "expertise",
        "voice",
        "default",
    }
    name = identity_frontmatter.get("name") or directory.name

    return Identity(
        name=name,
        description=identity_frontmatter.get("description", ""),
        display_name=identity_frontmatter.get("display_name", "") or name,
        language=identity_frontmatter.get("language", ""),
        expertise=identity_frontmatter.get("expertise", []) or [],
        voice=identity_frontmatter.get("voice", ""),
        default=bool(identity_frontmatter.get("default", False)),
        soul=soul,
        identity=identity_body,
        user=user,
        tools=tools,
        path=directory,
        meta={k: v for k, v in identity_frontmatter.items() if k not in known},
    )


def load_identities_from_dir(directory: str | Path) -> list[Identity]:
    """Load all identity packages from a directory."""
    directory = Path(directory)
    if not directory.is_dir():
        return []

    identities = []
    if _has_identity_files(directory):
        identities.append(parse_identity(directory))

    for child in sorted(directory.iterdir()):
        if child.is_dir() and _has_identity_files(child):
            identities.append(parse_identity(child))
    return identities
