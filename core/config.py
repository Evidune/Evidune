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
class AgentConfig:
    llm_provider: str = "openai"  # openai | anthropic | local
    llm_model: str = "gpt-4o"
    llm_base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    max_history: int = 20
    temperature: float = 0.7
    system_prompt: str = ""


@dataclass
class SkillsConfig:
    directories: list[str] = field(default_factory=lambda: ["skills/"])
    auto_update: bool = True  # Whether iteration loop updates skill docs


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
        agent = AgentConfig(
            llm_provider=agent_raw.get("llm_provider", "openai"),
            llm_model=agent_raw.get("llm_model", "gpt-4o"),
            llm_base_url=agent_raw.get("llm_base_url"),
            api_key_env=agent_raw.get("api_key_env", "OPENAI_API_KEY"),
            max_history=agent_raw.get("max_history", 20),
            temperature=agent_raw.get("temperature", 0.7),
            system_prompt=agent_raw.get("system_prompt", ""),
        )

    # Skills config
    skills_raw = raw.get("skills", {})
    skills_config = SkillsConfig(
        directories=skills_raw.get("directories", ["skills/"]),
        auto_update=skills_raw.get("auto_update", True),
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
        memory=memory_config,
        gateways=gateways,
    )
