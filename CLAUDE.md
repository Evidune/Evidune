# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -e ".[dev]"          # Install in dev mode
pip install -e ".[all,dev]"      # With all LLM providers

python -m pytest tests/ -v       # Run all tests
python -m pytest tests/test_updater.py -v                    # Single test file
python -m pytest tests/test_agent.py::TestAgentCore::test_handle_message -v  # Single test

aiflay serve -c aiflay.yaml     # Start agent (interactive mode with gateways)
aiflay run -c aiflay.yaml       # Run one iteration cycle (metrics → analysis → update → commit)
```

## Architecture

Aiflay is a lightweight AI agent framework that combines multi-channel messaging (from OpenClaw) with skill self-iteration (inspired by Hermes, but driven by real-world outcome metrics instead of execution traces).

**Two operational modes:**
- `serve` — interactive agent: gateways receive messages → AgentCore selects skills + loads memory → LLM generates response
- `run` — iteration loop: fetch metrics → analyze top/bottom performers → update skill reference docs → git commit → notify via channels

**Key data flow in serve mode:**
```
Gateway (cli/feishu) → InboundMessage → AgentCore
  → SkillRegistry.find_relevant(message) → inject as system prompt
  → MemoryStore.get_history() + get_facts() → add to context
  → LLMClient.complete(messages) → OutboundMessage → Gateway
```

**Key data flow in run mode:**
```
MetricsAdapter.fetch() → Analyzer.analyze() → Updater.update_reference()
  → GitOps.commit_changes() → Channel.send_report()
```

**Module responsibilities:**
- `gateway/` — **bidirectional** message handling (receive + respond). `router.py` starts all gateways and routes to AgentCore.
- `channels/` — **outbound-only** notifications for iteration reports. Separate from gateway.
- `skills/` — parses SKILL.md (YAML frontmatter + markdown body, OpenClaw-compatible). Registry injects relevant skills into the LLM system prompt, not as tool definitions.
- `memory/` — SQLite with three tables: `conversations`, `messages`, `facts`. Facts are key-value persistent knowledge; messages are conversation history.
- `core/updater.py` — three Markdown update strategies: `append_only` (deduplicated), `replace_section` (by heading), `full_replace`. The iteration loop only touches reference docs, never SKILL.md core instructions.
- `agent/llm.py` — `LocalClient` reuses `OpenAIClient` with custom base_url (Ollama, vLLM).

**Config:** Single `aiflay.yaml` file. When `agent:` section is absent, operates in iteration-only mode. Environment variables expanded via `${VAR_NAME}` syntax.

**Skill self-iteration:** Skills with `outcome_metrics: true` in frontmatter have their reference data sections auto-updated by the iteration loop based on real performance metrics (reads, upvotes, etc.), not execution traces.
