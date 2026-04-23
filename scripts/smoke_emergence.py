#!/usr/bin/env python3
"""End-to-end smoke test for the full agent + emergence pipeline.

Calls a REAL LLM. Costs a few cents per run. Use this to verify that
the whole stack — gateway-less — actually works with a configured
provider.

Usage:
    # Option 1: OpenAI API key
    export OPENAI_API_KEY=sk-...
    python scripts/smoke_emergence.py --provider openai --model gpt-4o-mini

    # Option 2: OpenAI Codex CLI auth (run `codex login` first)
    python scripts/smoke_emergence.py --provider codex --model gpt-5.4

    # Option 3: Anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
    python scripts/smoke_emergence.py --provider anthropic --model claude-sonnet-4-6

What it does:
1. Spins up an in-memory MemoryStore, empty SkillRegistry,
   one identity package, the chosen LLM client.
2. Plays a short scripted conversation that includes an explicit request
   to create a reusable incident-triage skill.
3. Verifies that the skill transaction/emergence path persists and
   registers the generated skill.
4. Reports what was extracted/emerged at the end.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

# Make repo modules importable when run as `python scripts/smoke_emergence.py`
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from agent.core import AgentCore  # noqa: E402
from agent.fact_extractor import FactExtractor  # noqa: E402
from agent.llm import create_llm_client  # noqa: E402
from agent.pattern_detector import PatternDetector  # noqa: E402
from agent.skill_synthesizer import SkillSynthesizer  # noqa: E402
from gateway.base import InboundMessage  # noqa: E402
from identities.loader import Identity  # noqa: E402
from identities.registry import IdentityRegistry  # noqa: E402
from memory.store import MemoryStore  # noqa: E402
from skills.registry import SkillRegistry  # noqa: E402

SCRIPTED_TURNS = [
    "I operate a local agent service and want a repeatable incident triage workflow.",
    (
        "Sample incident: a web deploy starts, /api/health is OK, but the browser shows "
        "a blank screen. Triage it with symptom, checks, likely cause, and next action."
    ),
    (
        "Create a reusable skill for this incident triage workflow. It should help future "
        "agents inspect logs, confirm config, check recent changes, and propose a safe fix. "
        "Include a Markdown checklist and source notes."
    ),
    (
        "Now apply that workflow to a second incident: HTTP 500 appears after a restart, "
        "and launch logs are mostly empty."
    ),
]


async def run(provider: str, model: str, base_url: str | None = None) -> None:
    print(f"Provider: {provider}  Model: {model}")
    print("=" * 60)

    api_key_env = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }.get(provider)
    api_key = os.environ.get(api_key_env) if api_key_env else None

    llm = create_llm_client(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0.5,
    )

    with tempfile.TemporaryDirectory() as tmp:
        memory = MemoryStore(Path(tmp) / "smoke.db")
        skill_registry = SkillRegistry()

        identity_registry = IdentityRegistry()
        identity_registry.register(
            Identity(
                name="ops-agent",
                display_name="Ops Agent",
                soul="You are concise, helpful, and calm.",
                identity="You help developers run, debug, and improve local agent systems.",
                user="The user wants reusable operational workflows and practical next steps.",
                default=True,
                path=Path("/tmp/identities/ops-agent"),
            )
        )

        # Use the SAME LLM as the judge for simplicity (in real use, use a
        # different model to avoid self-justification bias)
        fact_extractor = FactExtractor(judge=llm)
        pattern_detector = PatternDetector(judge=llm)
        skill_synthesizer = SkillSynthesizer(judge=llm, output_dir=Path(tmp) / "emerged")

        agent = AgentCore(
            llm=llm,
            skill_registry=skill_registry,
            memory=memory,
            identity_registry=identity_registry,
            fact_extractor=fact_extractor,
            fact_extraction_every_n_turns=4,
            fact_extraction_min_confidence=0.6,
            pattern_detector=pattern_detector,
            skill_synthesizer=skill_synthesizer,
            emergence_every_n_turns=6,
            emergence_min_confidence=0.6,
        )

        conv_id = "smoke-conv"
        for i, text in enumerate(SCRIPTED_TURNS, start=1):
            msg = InboundMessage(
                text=text,
                sender_id="smoke-user",
                channel="cli",
                conversation_id=conv_id,
            )
            print(f"\n[Turn {i}] >>> {text}")
            try:
                resp = await agent.handle(msg)
            except Exception as e:
                print(f"  ERROR: {e}")
                continue

            preview = resp.text[:120].replace("\n", " ")
            print(f"  <<< {preview}…" if len(resp.text) > 120 else f"  <<< {resp.text}")

            md = resp.metadata
            extras = []
            if md.get("facts_extracted"):
                extras.append(f"facts:+{md['facts_extracted']}")
            if md.get("emerged_skill"):
                extras.append(f"emerged:{md['emerged_skill']}")
            if md.get("skill_creation"):
                creation = md["skill_creation"]
                extras.append(
                    f"skill_creation:{creation.get('status')}:{creation.get('skill_name')}"
                )
            if extras:
                print(f"  [{'  '.join(extras)}]")

        background = await agent.wait_for_background_emergence(timeout_s=180)
        if background:
            print("\nBackground emergence completed:")
            for decision in background:
                status = (decision.skill_creation or {}).get("status") or decision.activation_status
                name = (
                    (decision.skill_creation or {}).get("skill_name")
                    or decision.emerged_skill
                    or decision.detected_name
                )
                print(f"  - {status}: {name or 'unknown'}")

        # Final report
        print("\n" + "=" * 60)
        print("RESULT")
        print("=" * 60)

        all_facts = memory.get_facts(namespace=None)
        print(f"\nFacts learned: {len(all_facts)}")
        for f in all_facts:
            print(f"  - {f.key}: {f.value}")

        all_skills = skill_registry.all()
        print(f"\nSkills in registry (incl. emerged): {len(all_skills)}")
        for s in all_skills:
            print(f"  - {s.name}: {s.description}")

        emerged = memory.list_emerged_skills()
        print(f"\nEmerged skills (in DB): {len(emerged)}")
        for e in emerged:
            print(f"  - {e['name']} (status={e['status']}, version={e['version']})")
            print(f"    rationale: {e['evaluation_criteria'][:100]}")

        if not emerged:
            raise SystemExit(
                "No emerged skill was persisted. Check LLM credentials, "
                "skill_creation metadata, and emergence logs."
            )


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--provider", default="openai", choices=["openai", "anthropic", "local", "codex"]
    )
    p.add_argument("--model", default="gpt-4o-mini")
    p.add_argument("--base-url", default=None)
    args = p.parse_args()

    asyncio.run(run(args.provider, args.model, args.base_url))


if __name__ == "__main__":
    main()
