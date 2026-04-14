"""Skill registry — index, search, and provide skills to the LLM.

Supports Claude-style progressive disclosure:
  Level 0 — name + description only (decide which skills to activate)
  Level 1 — full SKILL.md instructions (after activation)
  Level 2 — references/ documents loaded on demand
"""

from __future__ import annotations

from pathlib import Path

from skills.loader import Skill, load_skills_from_dir


class SkillRegistry:
    """Manages loaded skills and provides them to the agent."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    # --- Loading ---

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

    def register(self, skill: Skill) -> None:
        """Register a skill instance directly (e.g. for emerged skills)."""
        self._skills[skill.name] = skill

    # --- Lookup ---

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    def get_outcome_skills(self) -> list[Skill]:
        """Get skills that participate in outcome-driven iteration."""
        return [s for s in self._skills.values() if s.outcome_metrics]

    # --- Matching ---

    def find_relevant(self, query: str, max_results: int = 3) -> list[Skill]:
        """Find skills relevant to a query.

        Scoring (heuristic):
          +3  triggers phrase appears in query
          -5  anti-triggers phrase appears in query  (excludes the skill)
          +2  name match
          +2  tag match
          +1  per keyword overlap with description
        """
        if not self._skills:
            return []

        query_lower = query.lower()
        query_words = set(query_lower.split())
        scored: list[tuple[float, Skill]] = []

        for skill in self._skills.values():
            score = 0.0
            excluded = False

            # Anti-triggers: any match excludes the skill
            for anti in skill.anti_triggers:
                if self._phrase_in(anti, query_lower):
                    excluded = True
                    break
            if excluded:
                continue

            # Triggers
            for trigger in skill.triggers:
                if self._phrase_in(trigger, query_lower):
                    score += 3.0

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

    @staticmethod
    def _phrase_in(phrase: str, text: str) -> bool:
        """Naive phrase match: any non-trivial token from the phrase appears."""
        phrase_lower = phrase.lower().strip()
        if not phrase_lower:
            return False
        # Try the full phrase first
        if phrase_lower in text:
            return True
        # Then try keyword overlap (>=2 chars per token)
        tokens = [t for t in phrase_lower.split() if len(t) >= 3]
        return any(token in text for token in tokens)

    # --- Prompt rendering (progressive disclosure) ---

    def as_index_prompt(self, skills: list[Skill] | None = None) -> str:
        """Level 0: just names + descriptions (~30-50 tokens per skill)."""
        if skills is None:
            skills = self.all()
        if not skills:
            return ""

        lines = ["# Available Skills (index)"]
        for s in skills:
            lines.append(f"- **{s.name}** — {s.description}")
        return "\n".join(lines)

    def as_full_prompt(self, skills: list[Skill] | None = None) -> str:
        """Level 1: full instructions for activated skills."""
        if skills is None:
            skills = self.all()
        if not skills:
            return ""

        parts = ["# Active Skills\n"]
        for skill in skills:
            parts.append(f"## {skill.name}")
            if skill.description:
                parts.append(f"_{skill.description}_\n")
            if skill.triggers:
                parts.append("**Triggers:**")
                for t in skill.triggers:
                    parts.append(f"- {t}")
                parts.append("")
            if skill.anti_triggers:
                parts.append("**Do NOT use when:**")
                for t in skill.anti_triggers:
                    parts.append(f"- {t}")
                parts.append("")
            parts.append(skill.instructions)
            parts.append("")
        return "\n".join(parts)

    def as_system_prompt(self, skills: list[Skill] | None = None) -> str:
        """Default rendering — full prompt (backward compatible)."""
        return self.as_full_prompt(skills)

    def get_reference(self, skill_name: str, ref_name: str) -> str | None:
        """Level 2: load a specific reference document on demand."""
        skill = self._skills.get(skill_name)
        if not skill:
            return None
        return skill.references.get(ref_name)
