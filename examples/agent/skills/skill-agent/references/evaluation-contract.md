# skill-agent Evaluation Contract

## Success Criteria

- Explicit skill requests resolve through a skill transaction instead of generic advice.
- Created or updated skills use `SKILL.md`, Markdown `scripts/`, and Markdown `references/`.
- Lifecycle status is visible as created, updated, reused, queued, or failed with a concrete reason.

## Observable Signals

- Response metadata includes `skill_creation` when a transaction occurred.
- The skill registry and lifecycle tables reflect successful creation or update.

## Failure Modes

- Generic prose answer instead of a transaction.
- Duplicate skill created when an active similar skill exists.
- Missing lifecycle reason for failure or reuse.
