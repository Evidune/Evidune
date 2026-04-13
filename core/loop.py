"""Iteration loop orchestrator — the core of aiflay."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.analyzer import analyze
from core.config import AiflayConfig, load_config
from core.git_ops import commit_changes
from core.metrics import get_adapter
from core.updater import update_reference
from channels.base import IterationReport, create_channel


def run_iteration(config: AiflayConfig, base_dir: Path | None = None) -> IterationReport:
    """Execute one full iteration cycle.

    1. Fetch metrics via adapter
    2. Analyze top/bottom performers and extract patterns
    3. Update reference documents
    4. Git commit (if enabled)
    5. Send reports via channels

    Args:
        config: Parsed aiflay configuration.
        base_dir: Base directory for resolving relative paths. Defaults to cwd.

    Returns:
        IterationReport with all results.
    """
    if base_dir is None:
        base_dir = Path.cwd()

    # 1. Fetch metrics
    adapter = get_adapter(config.metrics.adapter)
    snapshot = adapter.fetch(config.metrics.config)
    snapshot.domain = config.domain

    # 2. Analyze
    sort_metric = config.metrics.config.get("sort_metric", "reads")
    result = analyze(
        snapshot,
        sort_metric=sort_metric,
        top_n=config.analysis.top_n,
        bottom_n=config.analysis.bottom_n,
    )

    # 3. Update reference documents
    updates = []
    for ref in config.references:
        ref_path = base_dir / ref.path

        # Build new content from analysis
        new_content = _build_reference_content(ref.update_strategy, ref.section, result)

        update = update_reference(
            path=ref_path,
            strategy=ref.update_strategy,
            new_content=new_content,
            section=ref.section,
        )
        updates.append(update)

    # 4. Git commit
    commit_sha = None
    if config.iteration.git_commit:
        changed_files = [u.path for u in updates if u.has_changes]
        if changed_files:
            commit_result = commit_changes(
                repo_path=base_dir,
                changed_files=changed_files,
                prefix=config.iteration.commit_prefix,
                summary=result.summary,
            )
            if commit_result.success:
                commit_sha = commit_result.sha

    # 5. Build report
    report = IterationReport(
        domain=config.domain,
        analysis=result,
        updates=updates,
        commit_sha=commit_sha,
    )

    # 6. Send via channels
    for ch_config in config.channels:
        channel = create_channel(
            ch_config.type,
            webhook=ch_config.webhook,
            **ch_config.config,
        )
        channel.send_report(report)

    return report


def _build_reference_content(
    strategy: str,
    section: str | None,
    result: "AnalysisResult",
) -> str:
    """Build new content for a reference document based on analysis results."""
    from core.analyzer import AnalysisResult

    lines = []

    if strategy == "replace_section" and section:
        # Rebuild the section with fresh data
        lines.append(f"{section}")
        lines.append("")

    if result.top_performers:
        lines.append("### Top Performers")
        for i, r in enumerate(result.top_performers, 1):
            metrics_str = ", ".join(f"{k}={v}" for k, v in r.metrics.items())
            lines.append(f"{i}. **{r.title}** — {metrics_str}")
        lines.append("")

    if result.bottom_performers:
        lines.append("### Bottom Performers")
        for r in result.bottom_performers:
            metrics_str = ", ".join(f"{k}={v}" for k, v in r.metrics.items())
            lines.append(f"- {r.title} — {metrics_str}")
        lines.append("")

    if result.patterns:
        lines.append("### Patterns")
        for p in result.patterns:
            lines.append(f"- {p}")
        lines.append("")

    return "\n".join(lines)


async def serve(config: AiflayConfig, base_dir: Path | None = None) -> None:
    """Start the agent with configured gateways.

    This is the interactive mode where the agent listens for messages
    on configured channels (CLI, Feishu, etc.) and responds using
    skills + memory + LLM.
    """
    import os
    from agent.llm import create_llm_client
    from agent.core import AgentCore
    from memory.store import MemoryStore
    from skills.registry import SkillRegistry
    from gateway.router import Router, create_gateway

    if base_dir is None:
        base_dir = Path.cwd()

    if not config.agent:
        print("Error: 'agent' section required in config for serve mode")
        sys.exit(1)

    # Initialize components
    api_key = os.environ.get(config.agent.api_key_env)
    llm = create_llm_client(
        provider=config.agent.llm_provider,
        model=config.agent.llm_model,
        api_key=api_key,
        base_url=config.agent.llm_base_url,
        temperature=config.agent.temperature,
    )

    memory = MemoryStore(config.memory.path)

    skill_registry = SkillRegistry()
    for skill_dir in config.skills.directories:
        skill_path = base_dir / skill_dir
        count = skill_registry.load_directory(skill_path)
        if count > 0:
            print(f"Loaded {count} skill(s) from {skill_path}")

    agent = AgentCore(
        llm=llm,
        skill_registry=skill_registry,
        memory=memory,
        system_prompt=config.agent.system_prompt,
        max_history=config.agent.max_history,
    )

    # Create gateways
    gateways_list = []
    if not config.gateways:
        # Default to CLI
        gateways_list.append(create_gateway("cli"))
    else:
        for gw_config in config.gateways:
            gateways_list.append(create_gateway(gw_config.type, **gw_config.config))

    router = Router(agent=agent, gateways=gateways_list)

    try:
        await router.start()
    except KeyboardInterrupt:
        pass
    finally:
        await router.stop()
        memory.close()


def main() -> None:
    """CLI entry point: aiflay run|serve [config_path]."""
    import asyncio

    parser = argparse.ArgumentParser(description="Aiflay — outcome-driven skill self-iteration")
    parser.add_argument("command", choices=["run", "serve"], help="Command to execute")
    parser.add_argument("--config", "-c", default="aiflay.yaml", help="Path to aiflay.yaml")
    parser.add_argument("--base-dir", "-d", default=None, help="Base directory for resolving paths")

    args = parser.parse_args()

    config = load_config(args.config)
    base_dir = Path(args.base_dir) if args.base_dir else Path(args.config).parent

    if args.command == "run":
        report = run_iteration(config, base_dir)
        if not any(ch.type == "stdout" for ch in config.channels):
            print(report.summary_text())
        sys.exit(0 if report.has_changes or report.analysis.total_records > 0 else 1)

    elif args.command == "serve":
        asyncio.run(serve(config, base_dir))


if __name__ == "__main__":
    main()
