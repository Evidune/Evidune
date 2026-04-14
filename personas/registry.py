"""Persona registry — index, lookup, and default fallback."""

from __future__ import annotations

from pathlib import Path

from personas.loader import Persona, load_personas_from_dir


class PersonaRegistry:
    """Manages loaded personas and resolves which one to use per request."""

    def __init__(self) -> None:
        self._personas: dict[str, Persona] = {}
        self._default_name: str | None = None

    def load_directory(self, directory: str | Path) -> int:
        loaded = load_personas_from_dir(directory)
        for p in loaded:
            self._personas[p.name] = p
            if p.default and self._default_name is None:
                self._default_name = p.name
        return len(loaded)

    def load_directories(self, directories: list[str | Path]) -> int:
        total = 0
        for d in directories:
            total += self.load_directory(d)
        return total

    def register(self, persona: Persona) -> None:
        self._personas[persona.name] = persona
        if persona.default and self._default_name is None:
            self._default_name = persona.name

    def set_default(self, name: str) -> None:
        if name not in self._personas:
            raise KeyError(f"Persona '{name}' not loaded")
        self._default_name = name

    def get(self, name: str) -> Persona | None:
        return self._personas.get(name)

    def all(self) -> list[Persona]:
        return list(self._personas.values())

    def default(self) -> Persona | None:
        if self._default_name and self._default_name in self._personas:
            return self._personas[self._default_name]
        # Fall back: first-loaded persona, or None
        return next(iter(self._personas.values()), None)

    def resolve(self, name: str | None = None) -> Persona | None:
        """Return the persona to use for a request.

        If `name` is provided, look it up; otherwise return the default.
        Returns None if no personas are loaded at all.
        """
        if name:
            return self.get(name)
        return self.default()

    def __len__(self) -> int:
        return len(self._personas)
