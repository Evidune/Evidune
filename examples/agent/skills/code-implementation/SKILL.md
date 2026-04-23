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
---

## Instructions

Use this skill when the task requires concrete implementation:

1. Inspect existing files before proposing changes.
2. Make focused edits and preserve unrelated work.
3. Run the narrowest useful validation first, then broader checks when needed.
4. Use HTTP and shell tools for API integration checks when available.
5. Report exact commands, changed files, and remaining risk.
6. If runtime tools are unavailable, state that as a blocker instead of pretending execution happened.
