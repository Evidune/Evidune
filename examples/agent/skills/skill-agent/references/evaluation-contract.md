# skill-agent Execution Contract

## Success Criteria

- Explicit skill requests resolve through a skill transaction instead of generic advice.
- Created or updated skills use `SKILL.md` and Markdown `references/`, with `scripts/` reserved for explicit helper code.
- Lifecycle status is visible as created, updated, reused, queued, or failed with a concrete reason.

## Observable Signals

- Response metadata includes `skill_creation` when a transaction occurred.
- The skill registry and lifecycle tables reflect successful creation or update.

## Failure Modes

- Generic prose answer instead of a transaction.
- Duplicate skill created when an active similar skill exists.
- Missing lifecycle reason for failure or reuse.
