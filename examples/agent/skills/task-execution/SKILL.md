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
update_section: '## Reference Data'
execution_contract:
  version: 1
  min_pass_score: 0.7
  rewrite_below_score: 0.55
  disable_below_score: 0.25
  min_samples_for_rewrite: 3
  min_samples_for_disable: 2
  criteria:
    - name: goal_completion
      description: The response completes the user's requested operational outcome.
      weight: 0.4
    - name: tool_grounding
      description: Claims and decisions are grounded in available tool output or explicit limits.
      weight: 0.35
    - name: durable_learning
      description: Reusable lessons are captured or routed to skill creation when appropriate.
      weight: 0.25
  observable_signals:
    - name: relevant_tool_trace
      description: Relevant tool calls or an explicit no-tool limitation are present.
      source: tool_trace
      weight: 0.3
  failure_modes:
    - skipped_required_verification
    - hallucinated_external_state
    - failed_to_capture_reusable_workflow
outcome_contract:
  entity: task
  primary_kpi: success_score
  supporting_kpis: [reuse_count]
  dimensions: [channel, outcome]
  window:
    current_days: 7
    baseline_days: 7
  min_sample_size: 2
  rewrite_policy:
    target: 90
    min_delta: 5
    require_segment: true
    severe_regression_delta: 15
  rollback_policy:
    max_negative_delta: 10
  reference_update_policy:
    max_segments: 3
    max_exemplars: 2
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
