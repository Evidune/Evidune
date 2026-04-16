# Skill Iteration

This spec defines the target behavior for Aiflay's skill self-iteration system.
It intentionally describes the desired end state and calls out where the current
implementation still falls short.

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

## Current Implementation Gap

The current codebase only partially closes this loop.

- `aiflay run` updates `Reference Data`-style sections but does not rewrite the
  core skill definition
- Outcome analysis is currently limited to simple heuristics, so direct skill
  rewrites are not yet evidence-backed
- Feedback signals and evaluator scores are stored, but they are not consumed by
  any runtime decision engine
- Emerged skills are recorded as `pending_review`, yet they are also injected
  into the live in-process registry immediately, which makes status and runtime
  behavior diverge
- Startup loads skills from configured skill directories, but it does not
  automatically reload active emerged skills from `agent.emergence.output_dir`
- There is no implemented audit, disable, or rollback flow for automatic skill
  changes beyond raw metadata storage
