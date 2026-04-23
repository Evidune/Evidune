"""Built-in identity package tools for progressive disclosure."""

from __future__ import annotations

from agent.tools.base import Tool
from identities.registry import IdentityRegistry


def identity_tools(identities: IdentityRegistry) -> list[Tool]:
    """Progressive-disclosure tools for assistant identity packages."""

    async def list_identities() -> list[dict]:
        default = identities.default()
        return [
            {
                "name": identity.name,
                "display_name": identity.display_name or identity.name,
                "description": identity.description,
                "language": identity.language,
                "voice": identity.voice,
                "default": bool(default and identity.name == default.name),
            }
            for identity in identities.all()
        ]

    async def get_identity(name: str) -> str:
        identity = identities.get(name)
        if not identity:
            return f"(no identity named {name!r})"

        parts = [
            f"# {identity.display_name or identity.name}",
            f"Name: {identity.name}",
        ]
        if identity.description:
            parts.append(f"Description: {identity.description}")
        meta_lines = []
        if identity.language:
            meta_lines.append(f"- language: {identity.language}")
        if identity.voice:
            meta_lines.append(f"- voice: {identity.voice}")
        if identity.expertise:
            meta_lines.append("- expertise: " + ", ".join(identity.expertise))
        if identity.default:
            meta_lines.append("- default: true")
        if identity.meta:
            for key, value in identity.meta.items():
                meta_lines.append(f"- {key}: {value}")
        if meta_lines:
            parts.append("## Metadata\n" + "\n".join(meta_lines))
        if identity.prompt:
            parts.append(identity.prompt)
        return "\n\n".join(parts)

    async def read_identity_file(identity_name: str, file: str) -> str:
        identity = identities.get(identity_name)
        if not identity:
            return f"(no identity named {identity_name!r})"
        allowed = {"SOUL.md", "IDENTITY.md", "USER.md", "TOOLS.md"}
        if file not in allowed:
            return f"(unsupported identity file {file!r}; allowed: {', '.join(sorted(allowed))})"
        path = identity.path / file
        if not path.is_file():
            return f"(no identity file {file!r} for identity {identity_name!r})"
        return path.read_text(encoding="utf-8")

    return [
        Tool(
            name="list_identities",
            description="List available assistant identity packages and their metadata.",
            parameters={"type": "object", "properties": {}},
            handler=list_identities,
        ),
        Tool(
            name="get_identity",
            description="Load the combined prompt sections for a specific identity package.",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            handler=get_identity,
        ),
        Tool(
            name="read_identity_file",
            description=(
                "Load a raw file from an identity package "
                "(SOUL.md, IDENTITY.md, USER.md, or TOOLS.md)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "identity_name": {"type": "string"},
                    "file": {
                        "type": "string",
                        "description": "One of SOUL.md, IDENTITY.md, USER.md, TOOLS.md",
                    },
                },
                "required": ["identity_name", "file"],
            },
            handler=read_identity_file,
        ),
    ]
