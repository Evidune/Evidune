"""Iteration loop orchestrator — the core of evidune."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from channels.base import IterationReport, create_channel
from core.analyzer import analyze
from core.config import EviduneConfig, load_config
from core.config_cli import (
    configure_model,
    configured_gateways,
    ensure_config_file,
    gateway_from_options,
    load_raw_config,
    missing_config_env_vars,
    normalize_gateway_type,
    remove_gateway,
    test_gateway,
    upsert_gateway,
    validate_config_structure,
    write_raw_config,
)
from core.docs_lint import lint_repo
from core.feishu_onboarding import (
    APP_ID_ENV,
    APP_SECRET_ENV,
    load_local_credentials,
    register_feishu_app,
    save_local_credentials,
)
from core.git_ops import commit_changes
from core.iteration_helpers import build_reference_content, update_outcome_skills
from core.iteration_history import (
    format_iteration_run,
    format_iteration_runs,
    record_iteration_report,
)
from core.metrics import get_adapter
from core.project_init import init_project
from core.runtime_paths import (
    resolve_emergence_output_dir,
    resolve_memory_path,
    resolve_metrics_config,
    resolve_runtime_dir,
)
from core.updater import update_reference


def run_iteration(config: EviduneConfig, base_dir: Path | None = None) -> IterationReport:
    """Execute one full iteration cycle.

    1. Fetch metrics via adapter
    2. Analyze top/bottom performers and extract patterns
    3. Update reference documents
    4. Git commit (if enabled)
    5. Send reports via channels

    Args:
        config: Parsed evidune configuration.
        base_dir: Base directory for resolving relative paths. Defaults to cwd.

    Returns:
        IterationReport with all results.
    """
    if base_dir is None:
        base_dir = Path.cwd()

    # 1. Fetch metrics
    metrics_config = resolve_metrics_config(config, base_dir)
    adapter = get_adapter(config.metrics.adapter)
    snapshot = adapter.fetch(metrics_config)
    snapshot.domain = config.domain

    # 2. Analyze
    sort_metric = metrics_config.get("sort_metric", "reads")
    result = analyze(
        snapshot,
        sort_metric=sort_metric,
        top_n=config.analysis.top_n,
        bottom_n=config.analysis.bottom_n,
    )

    from memory.store import MemoryStore

    memory = MemoryStore(resolve_memory_path(config, base_dir))

    try:
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

        # 3b. Self-iterate outcome skills (Evidune's unique differentiator)
        if config.skills.auto_update:
            updates.extend(update_outcome_skills(config, base_dir, snapshot, memory))

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

        run_id = record_iteration_report(memory, config, snapshot, report, sort_metric)
    finally:
        memory.close()
    report.extra["iteration_run_id"] = run_id

    # 6. Send via channels
    for ch_config in config.channels:
        channel_kwargs = dict(ch_config.config)
        if ch_config.webhook is not None:
            channel_kwargs["webhook"] = ch_config.webhook
        channel = create_channel(
            ch_config.type,
            **channel_kwargs,
        )
        channel.send_report(report)

    return report


def _load_persisted_emerged_skills(skill_registry, memory, output_dir: str | Path) -> int:
    """Load parseable emerged skills before applying unified skill-state filters."""
    from skills.loader import parse_skill

    root = Path(output_dir).expanduser()
    loaded = 0

    for record in memory.list_emerged_skills():
        skill_path = Path(record.get("path") or (root / record["name"] / "SKILL.md")).expanduser()
        if not skill_path.is_file():
            reason = "Active emerged skill path missing during startup reload"
            evidence = {"path": str(skill_path)}
            memory.set_skill_state(
                record["name"],
                "disabled",
                origin="emerged",
                path=str(skill_path),
                reason=reason,
                evidence=evidence,
            )
            memory.record_skill_lifecycle_event(
                record["name"],
                "disable",
                status="disabled",
                path=str(skill_path),
                harness_task_id="",
                reason=reason,
                evidence=evidence,
            )
            continue

        if skill_registry.get(record["name"]) is not None:
            continue

        try:
            skill = parse_skill(skill_path)
        except Exception as exc:
            reason = "Failed to parse active emerged skill during startup reload"
            evidence = {"path": str(skill_path), "error": str(exc)}
            memory.set_skill_state(
                record["name"],
                "disabled",
                origin="emerged",
                path=str(skill_path),
                reason=reason,
                evidence=evidence,
            )
            memory.record_skill_lifecycle_event(
                record["name"],
                "disable",
                status="disabled",
                path=str(skill_path),
                harness_task_id="",
                reason=reason,
                evidence=evidence,
            )
            continue

        skill_registry.register(skill, source="emerged")
        loaded += 1

    return loaded


def _load_active_emerged_skills(skill_registry, memory, output_dir: str | Path) -> int:
    """Backward-compatible alias for tests and older call sites."""
    return _load_persisted_emerged_skills(skill_registry, memory, output_dir)


def _apply_skill_state_overrides(skill_registry, memory) -> int:
    """Remove any skill whose persisted lifecycle state is not active."""
    removed = 0
    for status in ("pending_review", "disabled", "rolled_back"):
        for skill_state in memory.list_skill_states(status=status):
            if skill_registry.unregister(skill_state["skill_name"]):
                removed += 1
    return removed


def _sync_loaded_skill_states(skill_registry, memory) -> None:
    """Ensure active loaded skills have first-class lifecycle rows."""
    for skill in skill_registry.all():
        existing = memory.get_skill_state(skill.name)
        emerged = memory.get_emerged_skill(skill.name)
        origin = (
            existing["origin"]
            if existing is not None
            else ("emerged" if emerged is not None else "base")
        )
        status = existing["status"] if existing is not None else "active"
        memory.upsert_skill_state(
            skill.name,
            origin=origin,
            path=str(skill.path),
            status=status,
            reason=(existing or {}).get("reason", ""),
            evidence=(existing or {}).get("evidence", {}),
        )


def _skill_records_payload(skill_registry, memory) -> list[dict]:
    """Merge loaded skill metadata with persisted lifecycle state for APIs."""
    payload_by_name: dict[str, dict] = {}
    for record in skill_registry.records():
        payload = record.to_dict()
        state = memory.get_skill_state(record.name)
        emerged = memory.get_emerged_skill(record.name)
        if state is not None:
            payload["source"] = state["origin"]
            payload["status"] = state["status"]
            payload["path"] = state["path"] or payload["path"]
            payload["created_at"] = state["created_at"] or payload["created_at"]
            payload["updated_at"] = state["updated_at"] or payload["updated_at"]
            if state["status"] != "active":
                payload["load_error"] = state["reason"] or payload["load_error"]
        elif emerged is not None:
            payload["source"] = "emerged"
            payload["status"] = emerged["status"]
            payload["path"] = emerged["path"] or payload["path"]
            payload["created_at"] = emerged["created_at"] or payload["created_at"]
            payload["updated_at"] = emerged["updated_at"] or payload["updated_at"]
        if emerged is not None:
            payload["version"] = str(emerged["version"])
        contract = memory.get_skill_evaluation_contract(record.name)
        if contract is not None and not payload.get("execution_contract"):
            active_contract = contract.get("contract") or {}
            observable_names = [
                item.get("name")
                for item in (
                    active_contract.get("observable_signals")
                    or active_contract.get("observable_metrics")
                    or []
                )
                if isinstance(item, dict) and item.get("name")
            ]
            payload["execution_contract"] = {
                "version": active_contract.get("version", 1),
                "criteria": [
                    item.get("name")
                    for item in active_contract.get("criteria", [])
                    if isinstance(item, dict) and item.get("name")
                ],
                "observable_signals": observable_names,
                "observable_metrics": observable_names,
                "failure_modes": active_contract.get("failure_modes", []),
                "min_pass_score": active_contract.get("min_pass_score", 0.7),
                "rewrite_below_score": active_contract.get("rewrite_below_score", 0.55),
                "disable_below_score": active_contract.get("disable_below_score", 0.25),
                "min_samples_for_rewrite": active_contract.get("min_samples_for_rewrite", 3),
                "min_samples_for_disable": active_contract.get("min_samples_for_disable", 2),
            }
        payload_by_name[record.name] = payload

    for state in memory.list_skill_states():
        if state["skill_name"] in payload_by_name:
            continue
        if state["origin"] != "emerged" and state["status"] == "active":
            # Active base/project skills must be loaded from the current registry.
            # A persisted active state without a loaded package is stale data from
            # an older profile and should not appear as a runtime skill.
            continue
        evidence = state.get("evidence", {})
        payload_by_name[state["skill_name"]] = {
            "name": state["skill_name"],
            "description": "",
            "source": state["origin"],
            "status": state["status"],
            "version": "",
            "path": state["path"],
            "scripts": [],
            "references": [],
            "triggers": [],
            "tags": [],
            "execution_contract": None,
            "outcome_contract": None,
            "warnings": [],
            "created_at": state["created_at"],
            "updated_at": state["updated_at"],
            "last_loaded_at": "",
            "load_error": evidence.get("error", "") or state["reason"],
        }

    return sorted(
        payload_by_name.values(), key=lambda item: (item["status"] != "active", item["name"])
    )


def _handle_docs_command(base_dir: Path, subcommand: str | None) -> int:
    if subcommand not in (None, "lint"):
        raise ValueError("docs only supports the 'lint' subcommand")
    errors = lint_repo(base_dir)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Documentation lint passed for {base_dir.resolve()}")
    return 0


def _handle_iterations_command(
    config: EviduneConfig, base_dir: Path, subcommand: str | None, target: str | None
) -> int:
    from memory.store import MemoryStore

    memory = MemoryStore(resolve_memory_path(config, base_dir))
    try:
        if subcommand in (None, "list"):
            print(format_iteration_runs(memory.list_iteration_runs()))
            return 0
        if subcommand == "show":
            if not target:
                raise ValueError("iterations show requires a numeric run id")
            try:
                run_id = int(target)
            except ValueError as exc:
                raise ValueError("iterations show requires a numeric run id") from exc
            print(format_iteration_run(memory.get_iteration_run(run_id)))
            return 0
        raise ValueError("iterations only supports 'list' and 'show'")
    finally:
        memory.close()


def _handle_init_command(target: str | None, subcommand: str | None) -> int:
    if subcommand:
        raise ValueError("init does not support subcommands; use --path if needed")
    result = init_project(target or ".")
    print(f"Initialized starter project at {result.root}")
    print("Created:")
    for path in result.created_files:
        print(f"  - {path.relative_to(result.root)}")
    print("")
    print("Next steps:")
    print(f"  cd {result.root}")
    print("  evidune run --config evidune.yaml")
    print("  evidune serve --config evidune.yaml")
    return 0


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _print_missing_env_warning(config_path: Path, data: dict) -> None:
    missing = missing_config_env_vars(data)
    if missing:
        print(
            "Missing environment variables for configured secrets: "
            + ", ".join(missing)
            + f"\nSet them before running: evidune serve --config {config_path}"
        )


def _interactive_default(label: str, current: str | None, fallback: str = "") -> str:
    prompt = f"{label}"
    default = current or fallback
    if default:
        prompt += f" [{default}]"
    value = input(prompt + ": ").strip()
    return value or default


def _cli_value(
    value: str | None,
    *,
    interactive: bool,
    label: str,
    current: str | None,
    fallback: str = "",
) -> str:
    if value:
        return value
    if interactive:
        return _interactive_default(label, current, fallback)
    return current or fallback


def _gateway_from_cli_options(
    data: dict,
    config_path: Path,
    args: argparse.Namespace,
    kind: str,
) -> dict:
    gateway_type = normalize_gateway_type(kind)
    app_id_env = args.app_id_env
    app_secret_env = args.app_secret_env
    domain = args.domain
    if args.one_click:
        if gateway_type != "feishu_bot":
            raise ValueError("--one-click is only supported for the Feishu gateway")
        app_id_env = app_id_env or APP_ID_ENV
        app_secret_env = app_secret_env or APP_SECRET_ENV
        registration = register_feishu_app(
            domain_name=str(data.get("domain") or "Evidune"),
            open_browser=not args.no_open_browser,
        )
        credentials_path = save_local_credentials(
            config_path,
            {
                app_id_env: registration["app_id"],
                app_secret_env: registration["app_secret"],
            },
        )
        domain = domain or registration["api_domain"]
        print(f"Saved Feishu credentials to {credentials_path}")

    return gateway_from_options(
        gateway_type,
        host=args.host,
        port=args.port,
        app_id_env=app_id_env,
        app_secret_env=app_secret_env,
        domain=domain,
        reply_mode=args.reply_mode,
        allowed_open_ids=_split_csv(args.allowed_open_ids),
        allowed_chat_ids=_split_csv(args.allowed_chat_ids),
    )


def _handle_configure_command(config_path: Path, args: argparse.Namespace) -> int:
    section = args.section or "model"
    if section != "model":
        raise ValueError("configure currently supports only --section model")
    data = ensure_config_file(config_path)
    agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
    interactive = not args.non_interactive
    provider = _cli_value(
        args.provider,
        interactive=interactive,
        label="LLM provider",
        current=agent.get("llm_provider"),
        fallback="openai",
    )
    model = _cli_value(
        args.model,
        interactive=interactive,
        label="LLM model",
        current=agent.get("llm_model"),
        fallback="gpt-4o",
    )
    base_url = args.base_url
    if base_url is None and interactive:
        base_url = _interactive_default("LLM base URL (optional)", agent.get("llm_base_url"), "")
    api_key_env = _cli_value(
        args.api_key_env,
        interactive=interactive,
        label="API key environment variable",
        current=agent.get("api_key_env"),
        fallback="OPENAI_API_KEY",
    )
    configure_model(
        data,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key_env=api_key_env,
        evaluator_provider=args.evaluator_provider,
        evaluator_model=args.evaluator_model,
        evaluator_base_url=args.evaluator_base_url,
        evaluator_api_key_env=args.evaluator_api_key_env,
    )
    write_raw_config(config_path, data)
    validate_config_structure(config_path)
    print(f"Configured model in {config_path}")
    _print_missing_env_warning(config_path, data)
    return 0


def _handle_channels_command(config_path: Path, args: argparse.Namespace) -> int:
    action = args.subcommand or "list"
    if action == "list":
        data = load_raw_config(config_path)
        gateways = configured_gateways(data)
        if not gateways:
            print("No message gateways configured.")
            return 0
        for gateway in gateways:
            gateway_type = normalize_gateway_type(gateway.get("type"))
            details = {k: v for k, v in gateway.items() if k != "type"}
            print(f"{gateway_type}: {details}" if details else gateway_type)
        return 0
    if action == "add":
        data = ensure_config_file(config_path)
        gateway = _gateway_from_cli_options(
            data,
            config_path,
            args,
            args.target or args.channel or "cli",
        )
        upsert_gateway(data, gateway)
        write_raw_config(config_path, data)
        validate_config_structure(config_path)
        print(f"Added message gateway: {gateway['type']}")
        _print_missing_env_warning(config_path, data)
        return 0
    if action == "remove":
        data = ensure_config_file(config_path)
        removed = remove_gateway(data, args.target or "")
        write_raw_config(config_path, data)
        validate_config_structure(config_path)
        print(f"Removed {removed} message gateway(s).")
        return 0
    if action == "test":
        data = load_raw_config(config_path)
        target_type = normalize_gateway_type(args.target) if args.target else None
        gateways = [
            gw
            for gw in configured_gateways(data)
            if target_type is None or normalize_gateway_type(gw.get("type")) == target_type
        ]
        if not gateways:
            raise ValueError("No matching message gateway configured")
        ok = True
        for gateway in gateways:
            passed, message = test_gateway(gateway)
            ok = ok and passed
            print(message)
        return 0 if ok else 1
    raise ValueError("channels supports add, list, remove, and test")


def _handle_gateway_command(config_path: Path, args: argparse.Namespace) -> int:
    if (args.subcommand or "status") != "status":
        raise ValueError("gateway only supports the 'status' subcommand")
    data = load_raw_config(config_path)
    gateways = configured_gateways(data) or [{"type": "cli"}]
    ok = True
    for gateway in gateways:
        passed, message = test_gateway(gateway)
        ok = ok and passed
        print(message)
    return 0 if ok else 1


def _handle_onboard_command(config_path: Path, args: argparse.Namespace) -> int:
    data = ensure_config_file(config_path)
    agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
    interactive = not args.non_interactive
    provider = _cli_value(
        args.provider,
        interactive=interactive,
        label="LLM provider",
        current=agent.get("llm_provider"),
        fallback="openai",
    )
    model = _cli_value(
        args.model,
        interactive=interactive,
        label="LLM model",
        current=agent.get("llm_model"),
        fallback="gpt-4o",
    )
    api_key_env = _cli_value(
        args.api_key_env,
        interactive=interactive,
        label="API key environment variable",
        current=agent.get("api_key_env"),
        fallback="OPENAI_API_KEY",
    )
    configure_model(
        data,
        provider=provider,
        model=model,
        base_url=args.base_url,
        api_key_env=api_key_env,
        evaluator_provider=args.evaluator_provider,
        evaluator_model=args.evaluator_model,
        evaluator_base_url=args.evaluator_base_url,
        evaluator_api_key_env=args.evaluator_api_key_env,
    )
    channel = args.channel
    if not channel and interactive:
        channel = _interactive_default("Message gateway (cli/web/feishu)", None, "cli")
    if channel:
        upsert_gateway(
            data,
            _gateway_from_cli_options(
                data,
                config_path,
                args,
                channel,
            ),
        )
    write_raw_config(config_path, data)
    validate_config_structure(config_path)
    print(f"Onboarded Evidune config at {config_path}")
    _print_missing_env_warning(config_path, data)
    return 0


def _build_harness_services(config: EviduneConfig, base_dir: Path, config_path: Path | None):
    if not config.agent:
        raise ValueError("agent section required for harness runtime commands")

    from agent.harness.delivery import DeliveryConfig, DeliveryManager
    from agent.harness.maintenance import MaintenanceSweepRunner
    from agent.harness.runtime import HarnessRuntimeManager
    from agent.harness.validation import ValidationConfig, ValidationHarness

    runtime_manager = None
    if config.agent.harness.environment.enabled:
        runtime_manager = HarnessRuntimeManager(
            runtime_dir=Path(resolve_runtime_dir(config, base_dir)),
            base_dir=base_dir,
            source_config_path=config_path,
            service_host=config.agent.harness.environment.service_host,
            startup_timeout_s=config.agent.harness.environment.startup_timeout_s,
            healthcheck_path=config.agent.harness.environment.healthcheck_path,
        )

    validator = None
    if config.agent.harness.validation.enabled:
        validator = ValidationHarness(
            ValidationConfig(
                headless=config.agent.harness.validation.headless,
                slow_mo_ms=config.agent.harness.validation.slow_mo_ms,
            )
        )

    delivery_manager = None
    if config.agent.harness.delivery.enabled:
        delivery_manager = DeliveryManager(
            base_dir,
            DeliveryConfig(
                branch_prefix=config.agent.harness.delivery.branch_prefix,
                github_enabled=config.agent.harness.delivery.github_enabled,
                auto_stage_tracked=config.agent.harness.delivery.auto_stage_tracked,
                ci_poll_interval_s=config.agent.harness.delivery.ci_poll_interval_s,
                ci_timeout_s=config.agent.harness.delivery.ci_timeout_s,
            ),
        )

    return runtime_manager, validator, delivery_manager, MaintenanceSweepRunner(base_dir)


def _print_json(payload) -> None:
    import json

    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _load_runtime_environment(runtime_manager, environment_id: str | None):
    if runtime_manager is None:
        raise ValueError("harness environment support is disabled")
    if not environment_id:
        raise ValueError("environment id is required for this command")
    return runtime_manager.load_environment(environment_id)


def _handle_env_command(
    config: EviduneConfig,
    base_dir: Path,
    config_path: Path | None,
    subcommand: str | None,
    target: str | None,
) -> int:
    runtime_manager, _, _, _ = _build_harness_services(config, base_dir, config_path)
    action = subcommand or "status"
    if action == "up":
        environment = runtime_manager.create_environment(target or "manual")
        payload = environment.up()
        _print_json({"environment_id": environment.environment_id, **payload})
        return 0
    environment = _load_runtime_environment(runtime_manager, target)
    if action == "down":
        _print_json(environment.down())
        return 0
    if action == "restart":
        _print_json(environment.restart())
        return 0
    if action == "health":
        _print_json(environment.health())
        return 0
    if action == "status":
        _print_json(environment.status())
        return 0
    raise ValueError("env only supports up, down, restart, health, and status")


async def _handle_validate_command(
    config: EviduneConfig,
    base_dir: Path,
    config_path: Path | None,
    subcommand: str | None,
    *,
    environment_id: str | None,
    page_path: str,
    contains_text: str,
    visible_test_id: str,
    url_contains: str,
    session_id: str,
) -> int:
    if (subcommand or "run") != "run":
        raise ValueError("validate only supports the 'run' subcommand")
    runtime_manager, validator, _, _ = _build_harness_services(config, base_dir, config_path)
    if validator is None or runtime_manager is None:
        raise ValueError("validation support requires both runtime environments and validation")
    environment = (
        runtime_manager.load_environment(environment_id)
        if environment_id
        else runtime_manager.create_environment("validate")
    )
    opened = await validator.open_app(environment, session_id=session_id, path=page_path or "/")
    snapshot = await validator.snapshot_ui(environment, session_id=session_id)
    screenshot = await validator.capture_screenshot(
        environment, session_id=session_id, name="validate-run"
    )
    assertion = await validator.assert_ui_state(
        environment,
        session_id=session_id,
        contains_text=contains_text,
        visible_test_id=visible_test_id,
        url_contains=url_contains,
    )
    _print_json(
        {
            "environment_id": environment.environment_id,
            "opened": opened,
            "snapshot": snapshot,
            "screenshot": screenshot,
            "assertion": assertion,
        }
    )
    return 0 if assertion["ok"] else 1


def _handle_delivery_command(
    config: EviduneConfig,
    base_dir: Path,
    config_path: Path | None,
    subcommand: str | None,
    *,
    environment_id: str | None,
    files: list[str],
    branch: str,
    message: str,
    pr_title: str,
    pr_body: str,
) -> int:
    if (subcommand or "submit") != "submit":
        raise ValueError("delivery only supports the 'submit' subcommand")
    runtime_manager, _, delivery_manager, _ = _build_harness_services(config, base_dir, config_path)
    if delivery_manager is None or runtime_manager is None:
        raise ValueError("delivery support requires both runtime environments and delivery")
    environment = (
        runtime_manager.load_environment(environment_id)
        if environment_id
        else runtime_manager.create_environment("delivery")
    )
    result = delivery_manager.submit(
        environment,
        files=files,
        branch=branch,
        message=message,
        pr_title=pr_title,
        pr_body=pr_body,
    )
    _print_json({"environment_id": environment.environment_id, **result})
    return 0


def _handle_maintenance_command(
    config: EviduneConfig,
    base_dir: Path,
    config_path: Path | None,
    subcommand: str | None,
) -> int:
    if (subcommand or "sweep") != "sweep":
        raise ValueError("maintenance only supports the 'sweep' subcommand")
    _, _, _, maintenance_runner = _build_harness_services(config, base_dir, config_path)
    _print_json(maintenance_runner.sweep())
    return 0


async def serve(
    config: EviduneConfig,
    base_dir: Path | None = None,
    config_path: Path | None = None,
) -> None:
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

    memory = MemoryStore(resolve_memory_path(config, base_dir))

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

    from agent.self_evaluator import SelfEvaluator

    self_evaluator = SelfEvaluator(judge=_build_judge())

    # Fact extraction is a core serve capability.
    from agent.fact_extractor import FactExtractor

    fact_judge = _build_judge() if config.agent.fact_extraction.use_evaluator else llm
    fact_extractor = FactExtractor(judge=fact_judge)

    # Conversation emergence is a core serve capability.
    from agent.pattern_detector import PatternDetector
    from agent.skill_intent_detector import SkillIntentDetector
    from agent.skill_synthesizer import SkillSynthesizer

    emerge_judge = _build_judge() if config.agent.emergence.use_evaluator else llm
    skill_intent_detector = SkillIntentDetector(judge=emerge_judge)
    pattern_detector = PatternDetector(judge=emerge_judge)
    skill_synthesizer = SkillSynthesizer(
        judge=emerge_judge,
        output_dir=resolve_emergence_output_dir(config, base_dir),
    )
    loaded = _load_persisted_emerged_skills(
        skill_registry,
        memory,
        resolve_emergence_output_dir(config, base_dir),
    )
    if loaded > 0:
        print(f"Loaded {loaded} emerged skill(s) from persistence")

    filtered = _apply_skill_state_overrides(skill_registry, memory)
    if filtered > 0:
        print(f"Skipped {filtered} non-active skill(s) via lifecycle state")
    _sync_loaded_skill_states(skill_registry, memory)

    # Title generator (always on when agent is configured — cheap, high value)
    from agent.title_generator import TitleGenerator

    title_generator = TitleGenerator(llm=llm)

    graph_memory = None
    if config.memory.graph.enabled:
        from agent.graph_memory import GraphMemoryService

        graph_memory = GraphMemoryService.from_config(memory, config.memory.graph)

    # Tool registry: internal tools always; external tools when enabled
    from agent.tools.external import ExternalToolsConfig, external_tools
    from agent.tools.registry import ToolRegistry

    tool_registry = ToolRegistry()
    if config.agent.tools.self_management_enabled:
        from core.runtime_tools import runtime_tools

        tool_registry.register_many(
            runtime_tools(config_path=config_path or (base_dir / "evidune.yaml"), base_dir=base_dir)
        )
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

    runtime_manager, validation_harness, delivery_manager, maintenance_runner = (
        _build_harness_services(config, base_dir, config_path)
    )

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
        self_evaluator=self_evaluator,
        skill_intent_detector=skill_intent_detector,
        pattern_detector=pattern_detector,
        skill_synthesizer=skill_synthesizer,
        emergence_every_n_turns=config.agent.emergence.every_n_turns,
        emergence_min_confidence=config.agent.emergence.min_confidence,
        title_generator=title_generator,
        tool_registry=tool_registry,
        skill_contract_auto_update=config.skills.auto_update,
        harness_config=config.agent.harness,
        base_dir=base_dir,
        config_path=config_path,
        runtime_manager=runtime_manager,
        validation_harness=validation_harness,
        delivery_manager=delivery_manager,
        maintenance_runner=maintenance_runner,
        graph_memory=graph_memory,
        context_config=config.agent.context,
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

    for gw in gateways_list:
        if isinstance(gw, WebGateway):
            gw.set_skill_provider(lambda: _skill_records_payload(skill_registry, memory))
            gw.set_skill_lookup(skill_registry.get)
            gw.set_skill_change_handler(agent._reload_governed_skill)
            gw.set_memory_store(memory)

    router = Router(agent=agent, gateways=gateways_list)

    try:
        await router.start()
    except KeyboardInterrupt:
        pass
    finally:
        wait_for_emergence = getattr(agent, "wait_for_background_emergence", None)
        if wait_for_emergence:
            await wait_for_emergence(timeout_s=180)
        await router.stop()
        memory.close()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for run, serve, runtime, validation, delivery, and maintenance."""
    import asyncio

    parser = argparse.ArgumentParser(description="Evidune — outcome-driven skill self-evolution")
    parser.add_argument(
        "command",
        choices=[
            "run",
            "serve",
            "onboard",
            "configure",
            "channels",
            "gateway",
            "docs",
            "iterations",
            "init",
            "env",
            "validate",
            "delivery",
            "maintenance",
            "eval",
        ],
        help="Command to execute",
    )
    parser.add_argument("subcommand", nargs="?", help="Subcommand for the selected command")
    parser.add_argument("target", nargs="?", help="Optional target for the selected subcommand")
    parser.add_argument("--config", "-c", default="evidune.yaml", help="Path to evidune.yaml")
    parser.add_argument("--base-dir", "-d", default=None, help="Base directory for resolving paths")
    parser.add_argument("--path", help="Target path for 'init'")
    parser.add_argument(
        "--environment-id", help="Existing runtime environment id for validate/delivery"
    )
    parser.add_argument("--page-path", default="/", help="Page path for validate run")
    parser.add_argument("--contains-text", default="", help="Required text for validate run")
    parser.add_argument(
        "--visible-test-id", default="", help="Visible data-testid for validate run"
    )
    parser.add_argument("--url-contains", default="", help="Required URL fragment for validate run")
    parser.add_argument("--session-id", default="default", help="Validation session id")
    parser.add_argument("--files", nargs="*", default=[], help="Files to stage for delivery submit")
    parser.add_argument("--branch", default="", help="Branch name for delivery submit")
    parser.add_argument("--message", default="", help="Commit message for delivery submit")
    parser.add_argument("--pr-title", default="", help="Pull request title for delivery submit")
    parser.add_argument("--pr-body", default="", help="Pull request body for delivery submit")
    parser.add_argument("--manifest", default="", help="Evaluation corpus manifest")
    parser.add_argument("--catalog", default="", help="Pinned third-party Skill catalog")
    parser.add_argument("--split", default="development", help="Evaluation corpus split")
    parser.add_argument("--skill-path", default="", help="Parent or active SKILL.md path")
    parser.add_argument("--candidate-path", default="", help="Candidate SKILL.md path")
    parser.add_argument("--skill-name", default="", help="Skill name override for evaluation")
    parser.add_argument("--mutation", action="append", default=[], help="Known-bad mutation")
    parser.add_argument(
        "--with-baseline", action="store_true", help="Also execute a no-Skill baseline"
    )
    parser.add_argument(
        "--promote-on-success",
        action="store_true",
        help="Promote a provenance-linked candidate after all holdout gates pass",
    )
    parser.add_argument(
        "--iterate-on-failure",
        action="store_true",
        help="Stage an immutable candidate from attributed evaluation failures",
    )
    parser.add_argument("--trials", type=int, default=3, help="Trials per task and variant")
    parser.add_argument("--experiment-id", default="", help="Skill experiment id")
    parser.add_argument("--format", default="json", help="Evaluation report format")
    parser.add_argument("--reason", default="", help="Lifecycle transition reason")
    parser.add_argument("--dataset", default="dev", help="External benchmark dataset name")
    parser.add_argument("--source-commit", default="", help="Pinned 40-character source commit")
    parser.add_argument("--limit", type=int, default=30, help="Maximum imported corpus tasks")
    parser.add_argument("--section", default="", help="Section for configure")
    parser.add_argument("--provider", default="", help="LLM provider for configure/onboard")
    parser.add_argument("--model", default="", help="LLM model for configure/onboard")
    parser.add_argument("--base-url", default=None, help="LLM base URL for configure/onboard")
    parser.add_argument("--api-key-env", default="", help="API key environment variable name")
    parser.add_argument("--evaluator-provider", default="", help="Evaluator LLM provider")
    parser.add_argument("--evaluator-model", default="", help="Evaluator LLM model")
    parser.add_argument("--evaluator-base-url", default=None, help="Evaluator LLM base URL")
    parser.add_argument(
        "--evaluator-api-key-env", default="", help="Evaluator API key environment variable name"
    )
    parser.add_argument("--channel", default="", help="Message gateway for onboard")
    parser.add_argument("--host", default="", help="Gateway host")
    parser.add_argument("--port", type=int, default=None, help="Gateway port")
    parser.add_argument("--app-id-env", default="", help="Feishu app id environment variable name")
    parser.add_argument(
        "--app-secret-env", default="", help="Feishu app secret environment variable name"
    )
    parser.add_argument("--domain", default="", help="Gateway provider domain")
    parser.add_argument("--reply-mode", default="", help="Feishu reply mode")
    parser.add_argument("--allowed-open-ids", default="", help="Comma-separated Feishu open ids")
    parser.add_argument("--allowed-chat-ids", default="", help="Comma-separated Feishu chat ids")
    parser.add_argument(
        "--one-click",
        action="store_true",
        help="Create and configure a Feishu app through the official scan flow",
    )
    parser.add_argument(
        "--no-open-browser",
        action="store_true",
        help="Print the Feishu setup link without opening a browser",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Do not prompt; use provided flags and defaults",
    )

    args = parser.parse_args(argv)
    base_dir = Path(args.base_dir) if args.base_dir else Path(args.config).parent
    config_path = Path(args.config).resolve()

    try:
        if args.command not in {"docs", "init"}:
            load_local_credentials(config_path)
        if args.command == "docs":
            return _handle_docs_command(
                Path(args.base_dir) if args.base_dir else Path.cwd(),
                args.subcommand,
            )
        if args.command == "init":
            return _handle_init_command(args.path, args.subcommand)
        if args.command == "configure":
            return _handle_configure_command(config_path, args)
        if args.command == "channels":
            return _handle_channels_command(config_path, args)
        if args.command == "gateway":
            return _handle_gateway_command(config_path, args)
        if args.command == "onboard":
            return _handle_onboard_command(config_path, args)

        config = load_config(args.config)

        if args.command == "run":
            report = run_iteration(config, base_dir)
            if not any(ch.type == "stdout" for ch in config.channels):
                print(report.summary_text())
            return 0 if report.has_changes or report.analysis.total_records > 0 else 1
        if args.command == "serve":
            asyncio.run(serve(config, base_dir, config_path=config_path))
            return 0
        if args.command == "iterations":
            return _handle_iterations_command(config, base_dir, args.subcommand, args.target)
        if args.command == "env":
            return _handle_env_command(config, base_dir, config_path, args.subcommand, args.target)
        if args.command == "validate":
            return asyncio.run(
                _handle_validate_command(
                    config,
                    base_dir,
                    config_path,
                    args.subcommand,
                    environment_id=args.environment_id,
                    page_path=args.page_path or args.target or "/",
                    contains_text=args.contains_text,
                    visible_test_id=args.visible_test_id,
                    url_contains=args.url_contains,
                    session_id=args.session_id,
                )
            )
        if args.command == "delivery":
            return _handle_delivery_command(
                config,
                base_dir,
                config_path,
                args.subcommand,
                environment_id=args.environment_id,
                files=args.files,
                branch=args.branch,
                message=args.message,
                pr_title=args.pr_title,
                pr_body=args.pr_body,
            )
        if args.command == "maintenance":
            return _handle_maintenance_command(config, base_dir, config_path, args.subcommand)
        if args.command == "eval":
            from core.evaluation_cli import handle_evaluation_command

            return asyncio.run(handle_evaluation_command(config, base_dir, args))
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
