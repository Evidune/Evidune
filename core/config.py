"""Configuration loader for aiflay.yaml."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ReferenceConfig:
    path: str
    update_strategy: str = "append_only"  # append_only | replace_section | full_replace
    section: str | None = None  # for replace_section strategy

    def __post_init__(self) -> None:
        valid = {"append_only", "replace_section", "full_replace"}
        if self.update_strategy not in valid:
            raise ValueError(
                f"Invalid update_strategy '{self.update_strategy}', must be one of {valid}"
            )
        if self.update_strategy == "replace_section" and not self.section:
            raise ValueError("replace_section strategy requires a 'section' field")


@dataclass
class AnalysisConfig:
    compare_window_days: int = 7
    top_n: int = 5
    bottom_n: int = 3


@dataclass
class MetricsConfig:
    adapter: str = "generic_csv"
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class IterationConfig:
    schedule: str = "0 21 * * *"
    git_commit: bool = True
    commit_prefix: str = "chore(review): "


@dataclass
class ChannelConfig:
    type: str  # feishu | slack | stdout
    webhook: str | None = None
    template: str | None = None
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class EmergenceConfig:
    """Skill emergence: detect reusable patterns and synthesise SKILL.md.

    When enabled, every N turns the agent runs pattern detection on
    recent conversation; if a reusable pattern is found above
    min_confidence, a complete SKILL.md is generated and saved to
    output_dir, then registered with status 'pending_review'.
    """

    enabled: bool = False
    every_n_turns: int = 10
    min_confidence: float = 0.7
    output_dir: str = "~/.aiflay/emerged_skills"
    use_evaluator: bool = True  # If False, use main agent LLM


@dataclass
class FactExtractionConfig:
    """Auto fact extraction from conversation history.

    When enabled, every N turns the agent asks an LLM (the evaluator
    if configured, otherwise the main LLM) to scan recent messages
    and extract persistent facts worth remembering.
    """

    enabled: bool = False
    every_n_turns: int = 5
    min_confidence: float = 0.7
    use_evaluator: bool = True  # If False, use the main agent LLM


@dataclass
class ToolsConfig:
    """Tool-use configuration.

    Internal tools (memory/skills/conversations) are always on when
    the agent runs. External tools (shell/file/http/python/grep/glob)
    are opt-in via `external_enabled` since they can touch the real
    filesystem and network.
    """

    external_enabled: bool = False
    shell_timeout_s: int = 60
    shell_output_bytes: int = 20_000
    file_read_max_bytes: int = 200_000
    file_write_max_bytes: int = 500_000
    http_timeout_s: int = 30
    http_max_bytes: int = 500_000
    python_timeout_s: int = 30
    python_output_bytes: int = 20_000
    grep_max_hits: int = 200
    glob_max_hits: int = 200


@dataclass
class HarnessConfig:
    """Harness orchestration config shared by serve and run workflows."""

    strategy: str = "single"  # single | swarm
    simple_turn_threshold: int = 18
    default_squad: str = "general"
    max_worker_branches: int = 2
    max_rounds: int = 2
    token_budget: int = 20_000
    tool_call_budget: int = 16
    wall_clock_budget_s: int = 120
    stream_events: bool = True
    iteration_workflow_enabled: bool = True

    def __post_init__(self) -> None:
        valid = {"single", "swarm"}
        if self.strategy not in valid:
            raise ValueError(f"Invalid harness strategy '{self.strategy}', must be one of {valid}")


@dataclass
class EvaluatorConfig:
    """Cross-model evaluator config.

    Should use a DIFFERENT model from the main agent to avoid LLM
    self-justification bias. E.g. agent=Claude, evaluator=GPT-4.
    """

    llm_provider: str = "anthropic"  # default differs from agent (openai)
    llm_model: str = "claude-sonnet-4-6"
    llm_base_url: str | None = None
    api_key_env: str = "ANTHROPIC_API_KEY"


@dataclass
class AgentConfig:
    llm_provider: str = "openai"  # openai | anthropic | local
    llm_model: str = "gpt-4o"
    llm_base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    max_history: int = 20
    temperature: float = 0.7
    system_prompt: str = ""
    evaluator: EvaluatorConfig | None = None
    fact_extraction: FactExtractionConfig = field(default_factory=FactExtractionConfig)
    emergence: EmergenceConfig = field(default_factory=EmergenceConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    harness: HarnessConfig = field(default_factory=HarnessConfig)


@dataclass
class SkillsConfig:
    directories: list[str] = field(default_factory=lambda: ["skills/"])
    auto_update: bool = True  # Whether iteration loop updates skill docs
    prompt_mode: str = "auto"  # auto | full | index

    def __post_init__(self) -> None:
        valid = {"auto", "full", "index"}
        if self.prompt_mode not in valid:
            raise ValueError(f"Invalid prompt_mode '{self.prompt_mode}', must be one of {valid}")


@dataclass
class IdentitiesConfig:
    """Where to look for assistant identity packages."""

    directories: list[str] = field(default_factory=lambda: ["identities/"])
    default: str | None = None  # name of default identity; None → first loaded


@dataclass
class MemoryConfig:
    path: str = "~/.aiflay/memory.db"
    max_messages_per_conversation: int = 100


@dataclass
class GatewayConfig:
    type: str  # cli | feishu_bot
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class AiflayConfig:
    domain: str
    description: str = ""
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    references: list[ReferenceConfig] = field(default_factory=list)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    iteration: IterationConfig = field(default_factory=IterationConfig)
    channels: list[ChannelConfig] = field(default_factory=list)
    # Agent framework config (optional — when absent, iteration-only mode)
    agent: AgentConfig | None = None
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    identities: IdentitiesConfig = field(default_factory=IdentitiesConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    gateways: list[GatewayConfig] = field(default_factory=list)


_ENV_PATTERN = re.compile(r"\$\{([^}]+)}")


def _expand_env(value: str) -> str:
    """Replace ${VAR_NAME} with environment variable values."""

    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        env_val = os.environ.get(var_name)
        if env_val is None:
            raise ValueError(f"Environment variable '{var_name}' not set")
        return env_val

    return _ENV_PATTERN.sub(replacer, value)


def _expand_env_recursive(obj: Any) -> Any:
    """Recursively expand environment variables in strings."""
    if isinstance(obj, str):
        return _expand_env(obj)
    if isinstance(obj, dict):
        return {k: _expand_env_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_recursive(item) for item in obj]
    return obj


def load_config(path: str | Path) -> AiflayConfig:
    """Load and parse an aiflay.yaml configuration file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Config file must be a YAML mapping, got {type(raw).__name__}")

    raw = _expand_env_recursive(raw)

    domain = raw.get("domain")
    if not domain:
        raise ValueError("Config must specify a 'domain'")

    references = [ReferenceConfig(**ref) for ref in raw.get("references", [])]

    channels = [
        ChannelConfig(
            type=ch["type"],
            webhook=ch.get("webhook"),
            template=ch.get("template"),
            config={k: v for k, v in ch.items() if k not in ("type", "webhook", "template")},
        )
        for ch in raw.get("channels", [])
    ]

    metrics_raw = raw.get("metrics", {})
    metrics = MetricsConfig(
        adapter=metrics_raw.get("adapter", "generic_csv"),
        config=metrics_raw.get("config", {}),
    )

    analysis_raw = raw.get("analysis", {})
    analysis = AnalysisConfig(
        compare_window_days=analysis_raw.get("compare_window_days", 7),
        top_n=analysis_raw.get("top_n", 5),
        bottom_n=analysis_raw.get("bottom_n", 3),
    )

    iteration_raw = raw.get("iteration", {})
    iteration = IterationConfig(
        schedule=iteration_raw.get("schedule", "0 21 * * *"),
        git_commit=iteration_raw.get("git_commit", True),
        commit_prefix=iteration_raw.get("commit_prefix", "chore(review): "),
    )

    # Agent config (optional)
    agent = None
    agent_raw = raw.get("agent")
    if agent_raw:
        evaluator = None
        eval_raw = agent_raw.get("evaluator")
        if eval_raw:
            evaluator = EvaluatorConfig(
                llm_provider=eval_raw.get("llm_provider", "anthropic"),
                llm_model=eval_raw.get("llm_model", "claude-sonnet-4-6"),
                llm_base_url=eval_raw.get("llm_base_url"),
                api_key_env=eval_raw.get("api_key_env", "ANTHROPIC_API_KEY"),
            )
        fe_raw = agent_raw.get("fact_extraction", {})
        fact_extraction = FactExtractionConfig(
            enabled=fe_raw.get("enabled", False),
            every_n_turns=fe_raw.get("every_n_turns", 5),
            min_confidence=fe_raw.get("min_confidence", 0.7),
            use_evaluator=fe_raw.get("use_evaluator", True),
        )
        em_raw = agent_raw.get("emergence", {})
        emergence = EmergenceConfig(
            enabled=em_raw.get("enabled", False),
            every_n_turns=em_raw.get("every_n_turns", 10),
            min_confidence=em_raw.get("min_confidence", 0.7),
            output_dir=em_raw.get("output_dir", "~/.aiflay/emerged_skills"),
            use_evaluator=em_raw.get("use_evaluator", True),
        )
        tools_raw = agent_raw.get("tools", {}) or {}
        tools_cfg = ToolsConfig(
            external_enabled=tools_raw.get("external_enabled", False),
            shell_timeout_s=tools_raw.get("shell_timeout_s", 60),
            shell_output_bytes=tools_raw.get("shell_output_bytes", 20_000),
            file_read_max_bytes=tools_raw.get("file_read_max_bytes", 200_000),
            file_write_max_bytes=tools_raw.get("file_write_max_bytes", 500_000),
            http_timeout_s=tools_raw.get("http_timeout_s", 30),
            http_max_bytes=tools_raw.get("http_max_bytes", 500_000),
            python_timeout_s=tools_raw.get("python_timeout_s", 30),
            python_output_bytes=tools_raw.get("python_output_bytes", 20_000),
            grep_max_hits=tools_raw.get("grep_max_hits", 200),
            glob_max_hits=tools_raw.get("glob_max_hits", 200),
        )
        harness_raw = agent_raw.get("harness", {}) or {}
        harness_cfg = HarnessConfig(
            strategy=harness_raw.get("strategy", "single"),
            simple_turn_threshold=harness_raw.get("simple_turn_threshold", 18),
            default_squad=harness_raw.get("default_squad", "general"),
            max_worker_branches=harness_raw.get("max_worker_branches", 2),
            max_rounds=harness_raw.get("max_rounds", 2),
            token_budget=harness_raw.get("token_budget", 20_000),
            tool_call_budget=harness_raw.get("tool_call_budget", 16),
            wall_clock_budget_s=harness_raw.get("wall_clock_budget_s", 120),
            stream_events=harness_raw.get("stream_events", True),
            iteration_workflow_enabled=harness_raw.get("iteration_workflow_enabled", True),
        )
        agent = AgentConfig(
            llm_provider=agent_raw.get("llm_provider", "openai"),
            llm_model=agent_raw.get("llm_model", "gpt-4o"),
            llm_base_url=agent_raw.get("llm_base_url"),
            api_key_env=agent_raw.get("api_key_env", "OPENAI_API_KEY"),
            max_history=agent_raw.get("max_history", 20),
            temperature=agent_raw.get("temperature", 0.7),
            system_prompt=agent_raw.get("system_prompt", ""),
            evaluator=evaluator,
            fact_extraction=fact_extraction,
            emergence=emergence,
            tools=tools_cfg,
            harness=harness_cfg,
        )

    # Skills config
    skills_raw = raw.get("skills", {})
    skills_config = SkillsConfig(
        directories=skills_raw.get("directories", ["skills/"]),
        auto_update=skills_raw.get("auto_update", True),
        prompt_mode=skills_raw.get("prompt_mode", "auto"),
    )

    # Identity packages
    identities_raw = raw.get("identities", {})
    identities_config = IdentitiesConfig(
        directories=identities_raw.get("directories", ["identities/"]),
        default=identities_raw.get("default"),
    )

    # Memory config
    memory_raw = raw.get("memory", {})
    memory_config = MemoryConfig(
        path=memory_raw.get("path", "~/.aiflay/memory.db"),
        max_messages_per_conversation=memory_raw.get("max_messages_per_conversation", 100),
    )

    # Gateway configs
    gateways = [
        GatewayConfig(
            type=gw["type"],
            config={k: v for k, v in gw.items() if k != "type"},
        )
        for gw in raw.get("gateways", [])
    ]

    return AiflayConfig(
        domain=domain,
        description=raw.get("description", ""),
        metrics=metrics,
        references=references,
        analysis=analysis,
        iteration=iteration,
        channels=channels,
        agent=agent,
        skills=skills_config,
        identities=identities_config,
        memory=memory_config,
        gateways=gateways,
    )
