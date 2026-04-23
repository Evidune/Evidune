# Conversation Mode

Each conversation has a persisted operating mode:

- `plan`: the agent focuses on analysis, sequencing, risk framing, and maintaining a structured plan
- `execute`: the agent is expected to carry out work when feasible and keep the plan updated as execution progresses

Behavior rules:

- The current mode is stored per conversation and reused on later turns until changed
- The web UI can switch modes before sending a message
- Structured plans are conversation-scoped and persist alongside the mode
- Internal tools cover plan state, conversations, memory, skills, and identity packages
- In `plan` mode, execution-only tools are not exposed to the model
- In `execute` mode, the model can use both planning tools and execution tools
