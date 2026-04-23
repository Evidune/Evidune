"""Skill registry — index, search, and provide skills to the LLM."""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

from skills.evaluation import contract_summary
from skills.loader import Skill, load_skills_from_dir
from skills.models import (
    SkillMatch,
    SkillRecord,
    SkillSnapshot,
    estimate_tokens,
    skill_tokens,
    utc_now,
)


class SkillRegistry:
    """Manages loaded skills and provides them to the agent."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._records: dict[str, SkillRecord] = {}

    def load_directory(self, directory: str | Path, *, source: str = "base") -> int:
        """Load skills from a directory. Returns count of skills loaded."""
        skills = load_skills_from_dir(directory)
        for skill in skills:
            self._skills[skill.name] = skill
            self._records[skill.name] = self._record_from_skill(skill, source=source)
        return len(skills)

    def load_directories(self, directories: list[str | Path], *, source: str = "base") -> int:
        """Load skills from multiple directories. Later dirs override earlier."""
        total = 0
        for d in directories:
            total += self.load_directory(d, source=source)
        return total

    def register(self, skill: Skill, *, source: str = "base", status: str = "active") -> None:
        """Register a skill instance directly (e.g. for emerged skills)."""
        self._skills[skill.name] = skill
        self._records[skill.name] = self._record_from_skill(
            skill,
            source=source,
            status=status,
        )

    def unregister(self, name: str) -> bool:
        """Remove a skill from the live registry if it exists."""
        self._records.pop(name, None)
        return self._skills.pop(name, None) is not None

    def _record_from_skill(
        self,
        skill: Skill,
        *,
        source: str,
        status: str = "active",
        load_error: str = "",
    ) -> SkillRecord:
        loaded_at = utc_now()
        return SkillRecord(
            name=skill.name,
            description=skill.description,
            source=source,
            status=status,
            version=skill.version,
            path=str(skill.path),
            scripts=sorted(skill.scripts.keys()),
            references=sorted(skill.references.keys()),
            triggers=list(skill.triggers),
            tags=list(skill.tags),
            evaluation_contract=contract_summary(skill.evaluation_contract),
            created_at=loaded_at,
            updated_at=loaded_at,
            last_loaded_at=loaded_at,
            load_error=load_error,
        )

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    def records(self) -> list[SkillRecord]:
        return list(self._records.values())

    def get_outcome_skills(self) -> list[Skill]:
        """Get skills that participate in outcome-driven iteration."""
        return [s for s in self._skills.values() if s.outcome_metrics]

    def find_matches(self, query: str, max_results: int = 3) -> list[SkillMatch]:
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
        scored: list[SkillMatch] = []

        for skill in self._skills.values():
            score = 0.0
            excluded = False
            reasons: list[str] = []

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
                    reasons.append(f"trigger:{trigger}")

            # Name match
            if skill.name.lower() in query_lower or query_lower in skill.name.lower():
                score += 2.0
                reasons.append("name")

            # Tag match
            for tag in skill.tags:
                if tag.lower() in query_lower:
                    score += 2.0
                    reasons.append(f"tag:{tag}")

            # Description keyword overlap
            desc_words = set(skill.description.lower().split())
            overlap = query_words & desc_words
            score += len(overlap)
            if overlap:
                reasons.append(f"description_overlap:{','.join(sorted(overlap))}")

            if score > 0:
                scored.append(SkillMatch(skill=skill, score=score, reasons=reasons))

        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:max_results]

    def find_relevant(self, query: str, max_results: int = 3) -> list[Skill]:
        """Find skills relevant to a query."""
        return [match.skill for match in self.find_matches(query, max_results=max_results)]

    def find_similar(
        self,
        *,
        name: str,
        text: str = "",
        max_results: int = 3,
        min_score: float = 0.42,
    ) -> list[SkillMatch]:
        """Find likely duplicate/reusable skills for a proposed skill."""
        if not self._skills:
            return []

        query_name = (name or "").strip().lower()
        query_text = f"{query_name} {text or ''}".strip()
        query_tokens = skill_tokens(query_text)
        results: list[SkillMatch] = []

        for skill in self._skills.values():
            skill_name = skill.name.lower()
            skill_text = " ".join(
                [
                    skill.name,
                    skill.description,
                    " ".join(skill.tags),
                    " ".join(skill.triggers),
                ]
            )
            candidate_tokens = skill_tokens(skill_text)
            score = 0.0
            reasons: list[str] = []

            if query_name and query_name == skill_name:
                score = 1.0
                reasons.append("exact_name")
            elif query_name and (query_name in skill_name or skill_name in query_name):
                score = max(score, 0.85)
                reasons.append("name_substring")
            elif query_name:
                ratio = SequenceMatcher(None, query_name, skill_name).ratio()
                if ratio >= 0.68:
                    score = max(score, min(0.78, ratio))
                    reasons.append(f"name_similarity:{ratio:.2f}")

            overlap = query_tokens & candidate_tokens
            if len(overlap) >= 2:
                union = query_tokens | candidate_tokens
                jaccard = len(overlap) / max(1, len(union))
                score = max(score, min(0.82, 0.25 + len(overlap) * 0.08 + jaccard * 0.5))
                reasons.append(f"token_overlap:{','.join(sorted(overlap)[:8])}")

            lower_query = query_text.lower()
            for trigger in skill.triggers:
                if trigger and self._phrase_in(trigger, lower_query):
                    score = max(score, 0.74)
                    reasons.append(f"trigger:{trigger}")

            if score >= min_score:
                results.append(SkillMatch(skill=skill, score=round(score, 3), reasons=reasons))

        results.sort(key=lambda item: item.score, reverse=True)
        return results[:max_results]

    def snapshot(self, query: str = "", max_results: int = 3) -> SkillSnapshot:
        """Capture the current runtime skill view for diagnostics."""
        matches = self.find_matches(query, max_results=max_results) if query else []
        prompt = (
            self.as_index_prompt([m.skill for m in matches]) if matches else self.as_index_prompt()
        )
        return SkillSnapshot(
            records=self.records(),
            matches=matches,
            prompt_token_estimate=estimate_tokens(prompt),
        )

    @staticmethod
    def _phrase_in(phrase: str, text: str) -> bool:
        """Naive phrase match: any non-trivial token from the phrase appears."""
        phrase_lower = phrase.lower().strip()
        if not phrase_lower:
            return False
        if phrase_lower in text:
            return True
        tokens = [t for t in phrase_lower.split() if len(t) >= 3]
        return any(token in text for token in tokens)

    def as_index_prompt(self, skills: list[Skill] | None = None) -> str:
        """Level 0: just names + descriptions (~30-50 tokens per skill)."""
        if skills is None:
            skills = self.all()
        if not skills:
            return ""

        lines = [
            "# Available Skills (index)",
            "",
            "Load full skill details only when needed:",
            "- call `get_skill` before relying on a skill's detailed behavior",
            "- call `read_skill_reference` for deeper reference material",
            "",
        ]
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
