"""Built-in squad profiles for the swarm harness."""

from __future__ import annotations

from agent.harness.models import SquadProfile


def builtin_squad_profiles() -> dict[str, SquadProfile]:
    return {
        "general": SquadProfile(
            name="general",
            worker_branches=1,
            roles=["planner", "worker", "critic", "synthesizer"],
            description="Balanced default squad for multi-step problem solving.",
        ),
        "research": SquadProfile(
            name="research",
            worker_branches=2,
            roles=["planner", "worker", "worker", "critic", "synthesizer"],
            description="Two worker branches for comparison, research, and exploration.",
        ),
        "execution": SquadProfile(
            name="execution",
            worker_branches=1,
            roles=["planner", "worker", "critic", "synthesizer"],
            description="Single tool-heavy worker for implementation and execution tasks.",
            tool_heavy_worker=True,
        ),
        "iteration": SquadProfile(
            name="iteration",
            worker_branches=1,
            roles=[
                "evidence_collector",
                "rewrite_proposer",
                "safety_reviewer",
                "lifecycle_arbiter",
            ],
            description="Outcome-driven iteration workflow over one skill definition.",
        ),
    }


def get_squad_profile(name: str) -> SquadProfile:
    return builtin_squad_profiles().get(name, builtin_squad_profiles()["general"])
