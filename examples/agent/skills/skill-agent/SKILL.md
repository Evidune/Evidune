---
name: skill-agent
description: Create, update, reuse, and diagnose Claude/OpenClaw-style skill packages
version: 1.0.0
tags: [skills, lifecycle, emergence]
triggers:
  - create a skill
  - update a skill
  - diagnose skill matching
  - reusable capability
  - 建立 skill
  - 创建 skill
  - 创建能力
  - 更新 skill
outcome_metrics: false
---

## Instructions

Treat skills as first-class runtime objects:

1. Decide whether the user wants to create, update, reuse, or debug a skill.
2. Prefer standard packages with `SKILL.md`, optional `scripts/*.md`, and `references/*.md`.
3. Keep generated scripts prompt-readable unless the user explicitly asks for executable code.
4. Explain lifecycle state clearly: created, updated, reused, disabled, or failed.
5. When debugging, inspect registry state, match reasons, lifecycle events, and logs.

## Examples

### Explicit skill creation

When the user asks to establish a reusable capability, create or update a skill package and return the lifecycle result instead of only giving advice.
