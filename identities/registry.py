"""Identity registry — index, lookup, and default fallback."""

from __future__ import annotations

from pathlib import Path

from identities.loader import Identity, load_identities_from_dir


class IdentityRegistry:
    """Manages loaded identities and resolves which one to use per request."""

    def __init__(self) -> None:
        self._identities: dict[str, Identity] = {}
        self._default_name: str | None = None

    def load_directory(self, directory: str | Path) -> int:
        loaded = load_identities_from_dir(directory)
        for identity in loaded:
            self._identities[identity.name] = identity
            if identity.default and self._default_name is None:
                self._default_name = identity.name
        return len(loaded)

    def load_directories(self, directories: list[str | Path]) -> int:
        total = 0
        for directory in directories:
            total += self.load_directory(directory)
        return total

    def register(self, identity: Identity) -> None:
        self._identities[identity.name] = identity
        if identity.default and self._default_name is None:
            self._default_name = identity.name

    def set_default(self, name: str) -> None:
        if name not in self._identities:
            raise KeyError(f"Identity '{name}' not loaded")
        self._default_name = name

    def get(self, name: str) -> Identity | None:
        return self._identities.get(name)

    def all(self) -> list[Identity]:
        return list(self._identities.values())

    def default(self) -> Identity | None:
        if self._default_name and self._default_name in self._identities:
            return self._identities[self._default_name]
        return next(iter(self._identities.values()), None)

    def resolve(self, name: str | None = None) -> Identity | None:
        """Return the identity to use for a request."""
        if name:
            return self.get(name)
        return self.default()

    def __len__(self) -> int:
        return len(self._identities)
