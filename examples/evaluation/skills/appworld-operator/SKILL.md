---
name: appworld-operator
description: Complete stateful AppWorld tasks through documented app APIs with minimal side effects and explicit verification.
version: 1.0.0
---

# AppWorld Operator

## Instructions

1. Read the user task closely and identify the smallest required final-state changes.
2. Discover the exact app APIs and parameter schemas with `apis.api_docs`; do not guess API names or arguments.
3. Obtain supervisor credentials or profile data only when required for the requested operation.
4. Execute only the requested actions. Never create, delete, send, transfer, or modify unrelated data.
5. Verify the resulting state with non-mutating read APIs and correct any incomplete requested change.
6. Call `apis.supervisor.complete_task(...)` only after verification; include an answer when the task asks for information.

Keep intermediate results in variables across `appworld_execute` calls. Treat API errors as evidence to inspect documentation or state, not as permission to broaden the action.
