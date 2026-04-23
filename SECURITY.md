# Security Policy

## Developer Preview Scope

Evidune is a local-first Developer Preview for building self-iterating skill
agents. It is not a hosted SaaS product and does not provide multi-user
isolation, tenant boundaries, centralized audit logging, or production sandbox
guarantees.

Use it in a disposable checkout or a trusted local workspace when evaluating
untrusted prompts, generated skills, or external tool use.

## External Tool Boundary

When `agent.tools.external_enabled` is true, the agent can use enabled external
tools such as shell commands, file reads/writes, Python execution, grep/glob, and
HTTP requests within the configured limits. Treat prompts that can reach those
tools as code-execution authority over the configured workspace.

Recommended preview defaults:

- Point tool execution at a narrow project directory, not your home directory.
- Use a temporary worktree for public demos and untrusted prompts.
- Review generated skills before reusing them in sensitive projects.
- Disable or narrow external tools in configs used against private code or data.

## Secrets And Local State

Do not commit or share:

- `.env` files or environment dumps.
- OpenAI, Anthropic, GitHub, or other API keys.
- Codex authentication files such as `~/.codex/auth.json`.
- SQLite memory databases and runtime state directories.
- Generated logs that may contain prompts, tool output, credentials, or private
  file paths.

The repository `.gitignore` covers common local artifacts, but it cannot protect
secrets copied into tracked files or pasted into prompts.

## Codex Provider

The Codex provider uses local Codex CLI authentication after `codex login`.
Protect Codex tokens like API keys. Do not publish token files, command traces,
or logs that include authentication material.

## Reporting Security Issues

For Developer Preview, avoid posting exploit details, secrets, or private logs in
public issues. Use a private maintainer channel when one is available; otherwise
open a minimal public issue that states there is a security concern and wait for
a private handoff path.
