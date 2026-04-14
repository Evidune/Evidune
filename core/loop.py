"""Iteration loop orchestrator — the core of aiflay."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from channels.base import IterationReport, create_channel
from core.analyzer import AnalysisResult, analyze
from core.config import AiflayConfig, load_config
from core.git_ops import commit_changes
from core.metrics import get_adapter
from core.updater import update_reference


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

    # 3b. Self-iterate outcome skills (Aiflay's unique differentiator)
    if config.skills.auto_update:
        updates.extend(_update_outcome_skills(config, base_dir, result))

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


def _update_outcome_skills(
    config: AiflayConfig,
    base_dir: Path,
    result: AnalysisResult,
) -> list:
    """Update SKILL.md files for skills with outcome_metrics: true.

    For each such skill, replaces the skill's update_section (default
    "## Reference Data") with Top Performers + Patterns derived from
    the current analysis.

    Returns a list of UpdateResult objects appended to the main updates list.
    """
    from skills.registry import SkillRegistry

    registry = SkillRegistry()
    for skill_dir in config.skills.directories:
        registry.load_directory(base_dir / skill_dir)

    skill_updates = []
    for skill in registry.get_outcome_skills():
        section = skill.meta.get("update_section", "## Reference Data")
        new_content = _build_skill_reference_content(section, result)

        update = update_reference(
            path=skill.path,
            strategy="replace_section",
            new_content=new_content,
            section=section,
        )
        skill_updates.append(update)

    return skill_updates


def _build_skill_reference_content(
    section: str,
    result: AnalysisResult,
) -> str:
    """Build the skill's reference data section from analysis results.

    Output: heading + timestamp + Top Performers + Patterns.
    Bottom performers are omitted (not useful as positive guidance for skills).
    """
    from datetime import datetime, timezone

    lines = [section, ""]
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"*Auto-updated by aiflay on {timestamp}*")
    lines.append("")

    if result.top_performers:
        lines.append("### Top Performers")
        for i, r in enumerate(result.top_performers, 1):
            metrics_str = ", ".join(f"{k}={v}" for k, v in r.metrics.items())
            lines.append(f"{i}. **{r.title}** — {metrics_str}")
        lines.append("")

    if result.patterns:
        lines.append("### Patterns")
        for p in result.patterns:
            lines.append(f"- {p}")
        lines.append("")

    return "\n".join(lines)


def _build_reference_content(
    strategy: str,
    section: str | None,
    result: AnalysisResult,
) -> str:
    """Build new content for a reference document based on analysis results."""

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

    from agent.core import AgentCore
    from agent.llm import create_llm_client
    from gateway.router import Router, create_gateway
    from memory.store import MemoryStore
    from skills.registry import SkillRegistry

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
            gw = create_gateway(gw_config.type, **gw_config.config)
            gateways_list.append(gw)

    # Initialize web gateways with skill metadata
    from gateway.web import WebGateway

    skills_meta = [{"name": s.name, "description": s.description} for s in skill_registry.all()]
    for gw in gateways_list:
        if isinstance(gw, WebGateway):
            gw.set_skills(skills_meta)

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
