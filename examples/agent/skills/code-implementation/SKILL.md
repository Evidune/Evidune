---
name: code-implementation
description: Implement code changes, inspect files, run commands, call HTTP endpoints, and verify results with tools
version: 1.0.0
tags: [coding, api-integration, debugging, verification]
triggers:
  - write code
  - modify files
  - implement an API integration
  - run tests
  - debug the service
  - 写代码
  - 修改代码
  - 接入 API
  - 调试服务
outcome_metrics: false
evaluation_contract:
  version: 1
  min_pass_score: 0.7
  rewrite_below_score: 0.55
  disable_below_score: 0.25
  min_samples_for_rewrite: 3
  min_samples_for_disable: 2
  criteria:
    - name: implementation_progress
      description: The response performs or clearly scopes concrete code changes.
      weight: 0.35
    - name: verification_quality
      description: The result is validated with relevant tests, commands, logs, or explicit blockers.
      weight: 0.35
    - name: change_safety
      description: The work preserves unrelated changes and respects tool/security boundaries.
      weight: 0.3
  observable_metrics:
    - name: validation_command_recorded
      description: A validation command, test result, or explicit inability to validate is recorded.
      source: execution_metadata
      weight: 0.3
  failure_modes:
    - unverified_code_claim
    - overwrote_unrelated_changes
    - ignored_tool_boundary
---

## Instructions

Use this skill when the task requires concrete implementation:

1. Inspect existing files before proposing changes.
2. Make focused edits and preserve unrelated work.
3. Run the narrowest useful validation first, then broader checks when needed.
4. Use HTTP and shell tools for API integration checks when available.
5. Report exact commands, changed files, and remaining risk.
6. If runtime tools are unavailable, state that as a blocker instead of pretending execution happened.
