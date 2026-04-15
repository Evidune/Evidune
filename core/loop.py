"""Iteration loop orchestrator — the core of aiflay."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from channels.base import IterationReport, create_channel
from core.analyzer import analyze
from core.config import AiflayConfig, load_config
from core.docs_lint import lint_repo
from core.git_ops import commit_changes
from core.iteration_helpers import build_reference_content, update_outcome_skills
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
        new_content = build_reference_content(ref.update_strategy, ref.section, result)

        update = update_reference(
            path=ref_path,
            strategy=ref.update_strategy,
            new_content=new_content,
            section=ref.section,
        )
        updates.append(update)

    # 3b. Self-iterate outcome skills (Aiflay's unique differentiator)
    if config.skills.auto_update:
        updates.extend(update_outcome_skills(config, base_dir, result))

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

    from identities.registry import IdentityRegistry

    identity_registry = IdentityRegistry()
    for identity_dir in config.identities.directories:
        identity_path = base_dir / identity_dir
        count = identity_registry.load_directory(identity_path)
        if count > 0:
            print(f"Loaded {count} identity package(s) from {identity_path}")
    if config.identities.default and identity_registry.get(config.identities.default):
        identity_registry.set_default(config.identities.default)

    # Helper: build the (optional) evaluator LLM client lazily.
    def _build_judge():
        if config.agent.evaluator:
            ev = config.agent.evaluator
            return create_llm_client(
                provider=ev.llm_provider,
                model=ev.llm_model,
                api_key=os.environ.get(ev.api_key_env),
                base_url=ev.llm_base_url,
                temperature=0.1,
            )
        return llm

    # Optional fact extractor
    fact_extractor = None
    if config.agent.fact_extraction.enabled:
        from agent.fact_extractor import FactExtractor

        judge = _build_judge() if config.agent.fact_extraction.use_evaluator else llm
        fact_extractor = FactExtractor(judge=judge)

    # Optional skill emergence (pattern detector + synthesiser)
    pattern_detector = None
    skill_synthesizer = None
    if config.agent.emergence.enabled:
        from agent.pattern_detector import PatternDetector
        from agent.skill_synthesizer import SkillSynthesizer

        emerge_judge = _build_judge() if config.agent.emergence.use_evaluator else llm
        pattern_detector = PatternDetector(judge=emerge_judge)
        skill_synthesizer = SkillSynthesizer(
            judge=emerge_judge, output_dir=config.agent.emergence.output_dir
        )

    # Title generator (always on when agent is configured — cheap, high value)
    from agent.title_generator import TitleGenerator

    title_generator = TitleGenerator(llm=llm)

    # Tool registry: internal tools always; external tools when enabled
    from agent.tools.external import ExternalToolsConfig, external_tools
    from agent.tools.internal import conversation_tools, memory_tools, skill_tools
    from agent.tools.registry import ToolRegistry

    tool_registry = ToolRegistry()
    tool_registry.register_many(memory_tools(memory))
    tool_registry.register_many(skill_tools(skill_registry))
    tool_registry.register_many(conversation_tools(memory, current_conversation_id=""))
    if config.agent.tools.external_enabled:
        ext_cfg = ExternalToolsConfig(
            shell_timeout_s=config.agent.tools.shell_timeout_s,
            shell_output_bytes=config.agent.tools.shell_output_bytes,
            file_read_max_bytes=config.agent.tools.file_read_max_bytes,
            file_write_max_bytes=config.agent.tools.file_write_max_bytes,
            http_timeout_s=config.agent.tools.http_timeout_s,
            http_max_bytes=config.agent.tools.http_max_bytes,
            python_timeout_s=config.agent.tools.python_timeout_s,
            python_output_bytes=config.agent.tools.python_output_bytes,
            grep_max_hits=config.agent.tools.grep_max_hits,
            glob_max_hits=config.agent.tools.glob_max_hits,
        )
        tool_registry.register_many(external_tools(base_dir=base_dir, config=ext_cfg))

    agent = AgentCore(
        llm=llm,
        skill_registry=skill_registry,
        memory=memory,
        system_prompt=config.agent.system_prompt,
        skill_prompt_mode=config.skills.prompt_mode,
        max_history=config.agent.max_history,
        identity_registry=identity_registry,
        fact_extractor=fact_extractor,
        fact_extraction_every_n_turns=config.agent.fact_extraction.every_n_turns,
        fact_extraction_min_confidence=config.agent.fact_extraction.min_confidence,
        pattern_detector=pattern_detector,
        skill_synthesizer=skill_synthesizer,
        emergence_every_n_turns=config.agent.emergence.every_n_turns,
        emergence_min_confidence=config.agent.emergence.min_confidence,
        title_generator=title_generator,
        tool_registry=tool_registry,
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

    # Initialize web gateways with skill metadata + memory store for /api/feedback
    from gateway.web import WebGateway

    skills_meta = [{"name": s.name, "description": s.description} for s in skill_registry.all()]
    for gw in gateways_list:
        if isinstance(gw, WebGateway):
            gw.set_skills(skills_meta)
            gw.set_memory_store(memory)

    router = Router(agent=agent, gateways=gateways_list)

    try:
        await router.start()
    except KeyboardInterrupt:
        pass
    finally:
        await router.stop()
        memory.close()


def main() -> None:
    """CLI entry point: aiflay run|serve|docs."""
    import asyncio

    parser = argparse.ArgumentParser(description="Aiflay — outcome-driven skill self-iteration")
    parser.add_argument("command", choices=["run", "serve", "docs"], help="Command to execute")
    parser.add_argument("subcommand", nargs="?", help="Subcommand for the selected command")
    parser.add_argument("--config", "-c", default="aiflay.yaml", help="Path to aiflay.yaml")
    parser.add_argument("--base-dir", "-d", default=None, help="Base directory for resolving paths")

    args = parser.parse_args()

    if args.command == "docs":
        if args.subcommand not in (None, "lint"):
            parser.error("docs only supports the 'lint' subcommand")
        base_dir = Path(args.base_dir) if args.base_dir else Path.cwd()
        errors = lint_repo(base_dir)
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            sys.exit(1)
        print(f"Documentation lint passed for {base_dir.resolve()}")
        sys.exit(0)

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
