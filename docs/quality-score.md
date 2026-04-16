# Quality Score

Baseline assessment for the current repository. The intent is trend visibility,
not false precision.

| Area            | Grade | Notes                                                                                                |
| --------------- | ----- | ---------------------------------------------------------------------------------------------------- |
| `core/`         | B     | Solid iteration loop, but CLI and orchestration are still monolithic                                 |
| `agent/`        | B-    | Good feature velocity; prompt assembly and orchestration need further splitting                      |
| `memory/`       | B-    | Clear single entrypoint, but `store.py` is oversized                                                 |
| `skills/`       | B     | Strong loader/registry model; progressive disclosure now needs to stay default                       |
| `gateway/`      | B+    | Web and router flows now have deterministic browser coverage for execute/plan/feedback               |
| `web/`          | B     | Streaming timeline, plan mode, and feedback flows are browser-validated; richer lifecycle UX remains |
| `docs/`         | C     | Skeleton added; now needs ongoing execution plans and product specs                                  |
| CI / automation | B-    | CI now includes docs lint, full pytest, web build, and Chromium browser validation                   |

## Upgrade Priorities

1. Keep repo knowledge current and linked.
2. Prevent new architectural drift mechanically.
3. Expand agent-visible validation before adding more product surface area.
