---
name: task-execution
description: Execute general user tasks with tool-backed evidence and reusable skill feedback
version: 1.0.0
tags: [task-execution, verification, skill-iteration]
triggers:
  - execute this task
  - investigate and fix
  - verify with tools
  - diagnose the problem
  - turn this workflow into a skill
  - 执行任务
  - 排查问题
  - 验证结果
outcome_metrics: true
update_section: '## Reference Data'
---

## Instructions

Use this skill for general operational tasks:

1. Identify the requested outcome and constraints.
2. Use available tools for evidence: logs, files, HTTP calls, shell commands, tests, or database queries.
3. Keep the response grounded in what was actually checked or changed.
4. If the task reveals a repeatable capability, create, update, or reuse a skill package.
5. Preserve useful lessons in references so future runs improve.

## Reference Data

This section is replaced by `evidune run` after each iteration cycle.
