# Skill Iteration

This spec defines the behavior of Aiflay's skill self-iteration system after the
first harness-backed closure pass.

Skill iteration has two loops:

- `aiflay run`: outcome-driven updates to existing skills
- `aiflay serve`: conversation-driven emergence of new skills

## Target Behavior

### Outcome-Driven Iteration

When `skills.auto_update` is enabled, the iteration loop may update both the
evidence layer and the skill definition itself.

- Outcome-backed rewrites may change `Instructions`, `Triggers`,
  `Anti-Triggers`, and `Reference Data`
- The rewritten skill must retain durable evidence inside a reference section,
  including timestamped top performers and extracted patterns
- Update decisions should consider metrics snapshots, explicit user feedback
  signals, evaluator scores, and the recent performance history of the skill
- The loop may decide to keep the current skill unchanged, rewrite it, or roll
  it back to a previous version when newer edits underperform
- The decision workflow is recorded as a harness task with the roles
  `evidence_collector`, `rewrite_proposer`, `safety_reviewer`, and
  `lifecycle_arbiter`

### Conversation Emergence

When `agent.emergence.enabled` is on, high-confidence reusable patterns from
conversation should become active skills by default.

- A new skill is synthesised, written under `agent.emergence.output_dir`, and
  added to the live registry immediately
- Active emerged skills must be loaded again on later process starts so the
  behavior survives restart
- Name collisions must be prevented without silently overwriting an unrelated
  existing skill
- Manual review remains optional, but it is no longer the default gate for
  activation

### Lifecycle, Audit, and Rollback

Skill self-iteration needs a durable lifecycle model instead of one-off writes.

- `active` is the default state for a newly accepted emerged skill
- `pending_review` is reserved for explicit manual hold or operator override,
  not the default path
- `disabled` marks skills removed from matching but preserved for audit
- `rolled_back` records skills or rewrites reverted after negative evidence
- Every activation, rewrite, disable, and rollback must store the reason and
  evidence used to make that decision
- Runtime state, stored lifecycle state, and restart reload behavior must agree

### Unified Decision Inputs

Feedback and evaluation signals must feed the same decision loop.

- Web feedback signals such as `thumbs_up`, `thumbs_down`, `copied`,
  `regenerated`, and explicit rating must influence keep or rewrite decisions
- Cross-model evaluator scores must influence creation, promotion, rewrite, and
  rollback decisions
- No signal type should be stored indefinitely without affecting a future skill
  decision

## Acceptance Scenarios

- A newly emerged skill becomes matchable immediately after creation
- The same skill is still available after process restart
- Negative feedback or evaluator scores can disable or roll back a skill
- An outcome-tracked skill can rewrite its core instructions, not only replace a
  reference section
- Every automatic change leaves an auditable record that explains why it
  happened
- Each outcome iteration run persists a harness task, step, and artifact trail

## Current Gaps

The core loop is now closed, but a few product gaps remain:

- `pending_review` exists as lifecycle state, but there is no operator-facing UI
  for manual hold and resume flows yet
- The iteration workflow is deterministic and evidence-backed, but it does not
  yet use an LLM-driven reviewer or arbiter
- Disable semantics are strongest for emerged skills; non-emerged base skills
  still default to keep-or-rollback instead of a hard disable path
