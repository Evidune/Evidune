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
execution_contract:
  version: 1
  min_pass_score: 0.7
  rewrite_below_score: 0.55
  disable_below_score: 0.25
  min_samples_for_rewrite: 3
  min_samples_for_disable: 2
  criteria:
    - name: transaction_outcome
      description: The request is resolved as created, updated, reused, queued, or failed with a concrete reason.
      weight: 0.4
    - name: package_quality
      description: Created or updated skills use SKILL.md plus Markdown scripts and references.
      weight: 0.3
    - name: lifecycle_clarity
      description: The response and metadata explain lifecycle state and next availability.
      weight: 0.3
  observable_signals:
    - name: skill_creation_metadata
      description: Response metadata includes the skill creation status when a transaction occurred.
      source: execution_metadata
      weight: 0.3
  failure_modes:
    - generic_advice_instead_of_transaction
    - duplicate_skill_created
    - missing_lifecycle_reason
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
