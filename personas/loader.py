"""Persona loader — structured assistant identity definitions.

Each persona lives at `<dir>/<persona-name>/PERSONA.md` (preferred)
or `<dir>/PERSONA.md` (single inline persona). It defines a distinct
assistant identity: voice, expertise, language, default behaviour.

Personas are independent of skills:
  - A skill = a reusable capability ("write Zhihu article")
  - A persona = who is doing it ("老拐, a senior Zhihu writer")

A conversation is associated with exactly one persona. Multiple
personas can run concurrently (one Aiflay instance, multiple
assistants), each with its own facts namespace and system prompt.

Frontmatter:
  name           required  kebab-case identifier
  display_name   optional  human-readable name shown in UI
  description    optional  one-liner for indexes and lookups
  language       optional  primary language (e.g. zh-CN, en)
  expertise      optional  list of domains
  voice          optional  short tone descriptor
  default        optional  if true, this is the fallback persona

The markdown body is the system-prompt content describing the
identity in detail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class Persona:
    """A loaded persona / assistant identity."""

    name: str
    description: str = ""
    display_name: str = ""
    language: str = ""
    expertise: list[str] = field(default_factory=list)
    voice: str = ""
    default: bool = False
    body: str = ""  # full markdown body, injected as system prompt
    path: Path = Path()
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def namespace(self) -> str:
        """Memory namespace for this persona's facts."""
        return f"persona:{self.name}"


def parse_persona(path: str | Path) -> Persona:
    """Parse a PERSONA.md file into a Persona object."""
    path = Path(path)
    content = path.read_text(encoding="utf-8")

    match = _FRONTMATTER_RE.match(content)
    if not match:
        return Persona(name=path.stem, body=content.strip(), path=path)

    fm = yaml.safe_load(match.group(1)) or {}
    body = content[match.end() :].strip()

    known = {
        "name",
        "display_name",
        "description",
        "language",
        "expertise",
        "voice",
        "default",
    }

    return Persona(
        name=fm.get("name", path.stem),
        description=fm.get("description", ""),
        display_name=fm.get("display_name", "") or fm.get("name", path.stem),
        language=fm.get("language", ""),
        expertise=fm.get("expertise", []) or [],
        voice=fm.get("voice", ""),
        default=bool(fm.get("default", False)),
        body=body,
        path=path,
        meta={k: v for k, v in fm.items() if k not in known},
    )


def load_personas_from_dir(directory: str | Path) -> list[Persona]:
    """Load all personas from a directory.

    Recognised layouts:
      <dir>/<persona-name>/PERSONA.md   (preferred, Claude/OpenClaw style)
      <dir>/PERSONA.md                  (single inline persona)
      <dir>/<name>-PERSONA.md           (legacy compat)
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []

    personas = []
    for f in sorted(directory.iterdir()):
        if f.is_dir():
            pf = f / "PERSONA.md"
            if pf.exists():
                personas.append(parse_persona(pf))
        elif f.name == "PERSONA.md" or (f.suffix == ".md" and f.name.endswith("-PERSONA.md")):
            personas.append(parse_persona(f))
    return personas
