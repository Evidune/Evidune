#!/usr/bin/env python3
"""Smoke test: ask the real LLM to use tools (read a file + set a fact).

Usage:
    python scripts/smoke_tools.py --provider codex --model gpt-5.4
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from agent.core import AgentCore  # noqa: E402
from agent.llm import create_llm_client  # noqa: E402
from agent.tools.external import ExternalToolsConfig, external_tools  # noqa: E402
from agent.tools.internal import memory_tools  # noqa: E402
from agent.tools.registry import ToolRegistry  # noqa: E402
from gateway.base import InboundMessage  # noqa: E402
from memory.store import MemoryStore  # noqa: E402
from skills.registry import SkillRegistry  # noqa: E402


async def run(provider: str, model: str) -> None:
    print(f"Provider: {provider}  Model: {model}")
    print("=" * 60)

    api_key = os.environ.get(
        {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(provider, "")
    )
    llm = create_llm_client(provider=provider, model=model, api_key=api_key, temperature=0.2)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        # Seed a file for the agent to find
        (base / "notes.txt").write_text("the secret phrase is: orange-garage-42\n")

        memory = MemoryStore(base / "m.db")
        tools = ToolRegistry()
        tools.register_many(memory_tools(memory))
        tools.register_many(
            external_tools(base_dir=base, config=ExternalToolsConfig(shell_timeout_s=10))
        )

        agent = AgentCore(
            llm=llm,
            skill_registry=SkillRegistry(),
            memory=memory,
            tool_registry=tools,
            max_tool_iterations=6,
            system_prompt=(
                "You have tools to read files, run shell, and remember facts. "
                "Use them when the user asks you to."
            ),
        )

        prompt = (
            "Please read the file `notes.txt` in the project, extract the secret phrase, "
            "and then store it in memory as `user.secret_phrase`. Confirm what you stored."
        )
        msg = InboundMessage(
            text=prompt, sender_id="smoke", channel="cli", conversation_id="smoke-conv"
        )
        print(f">>> {prompt}\n")
        resp = await agent.handle(msg)

        print("<<<", resp.text)
        print()
        print("-- Tool trace --")
        for i, t in enumerate(resp.metadata.get("tool_trace", []), 1):
            err = " [ERROR]" if t["is_error"] else ""
            result = t["result"]
            if len(result) > 200:
                result = result[:200] + "…"
            print(f"{i}. {t['name']}({t['arguments']}){err}")
            print(f"   → {result}")

        print()
        print("-- Memory facts --")
        for f in memory.get_facts(namespace=None):
            print(f"  {f.key}: {f.value}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--provider", default="codex", choices=["openai", "codex", "anthropic"])
    p.add_argument("--model", default="gpt-5.4")
    args = p.parse_args()
    asyncio.run(run(args.provider, args.model))


if __name__ == "__main__":
    main()
