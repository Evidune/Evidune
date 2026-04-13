"""Skill registry — index, search, and provide skills to the LLM."""

from __future__ import annotations

from pathlib import Path

from skills.loader import Skill, load_skills_from_dir


class SkillRegistry:
    """Manages loaded skills and provides them to the agent."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def load_directory(self, directory: str | Path) -> int:
        """Load skills from a directory. Returns count of skills loaded."""
        skills = load_skills_from_dir(directory)
        for skill in skills:
            self._skills[skill.name] = skill
        return len(skills)

    def load_directories(self, directories: list[str | Path]) -> int:
        """Load skills from multiple directories. Later dirs override earlier."""
        total = 0
        for d in directories:
            total += self.load_directory(d)
        return total

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    def get_outcome_skills(self) -> list[Skill]:
        """Get skills that participate in outcome-driven iteration."""
        return [s for s in self._skills.values() if s.outcome_metrics]

    def find_relevant(self, query: str, max_results: int = 3) -> list[Skill]:
        """Find skills relevant to a query using keyword/tag matching.

        Simple scoring: +2 for name match, +2 for tag match, +1 per keyword in description.
        """
        if not self._skills:
            return []

        query_lower = query.lower()
        query_words = set(query_lower.split())
        scored: list[tuple[float, Skill]] = []

        for skill in self._skills.values():
            score = 0.0

            # Name match
            if skill.name.lower() in query_lower or query_lower in skill.name.lower():
                score += 2.0

            # Tag match
            for tag in skill.tags:
                if tag.lower() in query_lower:
                    score += 2.0

            # Description keyword overlap
            desc_words = set(skill.description.lower().split())
            overlap = query_words & desc_words
            score += len(overlap)

            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:max_results]]

    def as_system_prompt(self, skills: list[Skill] | None = None) -> str:
        """Format skills as system prompt context for the LLM.

        If skills is None, includes all loaded skills.
        """
        if skills is None:
            skills = self.all()

        if not skills:
            return ""

        parts = ["# Available Skills\n"]
        for skill in skills:
            parts.append(f"## {skill.name}")
            if skill.description:
                parts.append(f"*{skill.description}*\n")
            parts.append(skill.instructions)
            parts.append("")  # blank line between skills

        return "\n".join(parts)
