# Developer Preview Smoke

Use this checklist before sharing a checkout with another developer. These
checks are intentionally small: they prove that installation, LLM auth, external
tools, memory, and skill creation work together without claiming production
readiness.

## Prerequisites

- Python 3.10+ and `pip install -e ".[all,dev]"`.
- One LLM credential path:
  - `OPENAI_API_KEY` for `--provider openai`.
  - `ANTHROPIC_API_KEY` for `--provider anthropic`.
  - `codex login` for `--provider codex`.
- Node 20 only when building or serving the web UI.
- Playwright browsers only for browser E2E checks:
  `python -m playwright install chromium`.

## Gateway-less Tool Smoke

Run from the repository root:

```bash
python scripts/smoke_tools.py --provider openai --model gpt-4o-mini
```

Codex variant:

```bash
python scripts/smoke_tools.py --provider codex --model gpt-5.4
```

Expected result:

- The assistant reads `notes.txt` from a temporary workspace.
- The tool trace shows at least one file/shell-style tool call.
- Memory contains `user.secret_phrase: orange-garage-42`.

## Gateway-less Skill Creation Smoke

Run from the repository root:

```bash
python scripts/smoke_emergence.py --provider openai --model gpt-4o-mini
```

Codex variant:

```bash
python scripts/smoke_emergence.py --provider codex --model gpt-5.4
```

Expected result:

- The scripted conversation explicitly asks for a reusable incident-triage skill.
- The response metadata includes `skill_creation` or `emerged_skill`.
- The final report shows at least one persisted row under `Emerged skills (in DB)`.
- The generated skill package is loaded into the in-process registry.

## Interactive `serve` Smoke

Use a disposable directory:

```bash
tmpdir="$(mktemp -d)"
evidune init --path "$tmpdir/demo"
cd "$tmpdir/demo"
evidune serve --config evidune.yaml
```

In the interactive prompt, verify one external tool task:

```text
Use your available tools to inspect the current directory and tell me which
Evidune files exist here.
```

Then verify explicit skill creation:

```text
Create a reusable incident-triage skill for local agent services. Include a
checklist for logs, config, recent deploy changes, and safe rollback decisions.
```

Expected result:

- The agent performs tool-backed inspection when external tools are enabled.
- The explicit skill request returns a created, updated, or reused skill status.
- A generated package appears under the configured `agent.emergence.output_dir`.
- Restarting `evidune serve` reloads the active generated skill.

## Known Preview Limits

- Real LLM wording varies. Validate durable outputs, metadata, files, and SQLite
  rows rather than exact assistant prose.
- External tools can mutate the configured workspace. Use disposable directories
  for smoke tests and demos.
- `examples/agent/evidune.deploy.yaml` is a deployment example, not a hardened
  production security profile.
- Hosted SaaS, multi-user isolation, cloud monitoring, and marketplace-style
  skill distribution are roadmap items.
