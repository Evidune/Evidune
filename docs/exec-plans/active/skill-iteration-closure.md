# Skill Iteration Closure

- Title: Close the skill self-iteration loop
- Driving area: `core/`, `agent/`, `skills/`, `memory/`, `gateway/`
- Status: active

## Goal

Move skill self-iteration from a partial loop into a durable product behavior:

- emerged skills auto-activate and survive restart
- feedback and evaluator scores influence skill decisions
- outcome-driven iteration can rewrite skill definitions, not just evidence
- every automatic skill change is auditable and reversible

## Current State

- `evidune run` updates reference sections for `outcome_metrics: true` skills
- `evidune serve` can detect reusable patterns and synthesise new skills
- feedback signals and evaluator scores are persisted but not consumed
- emerged skills are marked `pending_review` in storage but are still inserted
  into the live process registry
- restart does not reload emerged skills from the configured emergence output
  directory

## Decisions

- Target behavior defaults to automatic activation, not review-first activation
- `pending_review` remains available as an explicit operator override, not the
  default lifecycle state
- Outcome-driven iteration is expected to rewrite the skill definition itself
  when evidence is strong enough
- Reference sections remain part of the skill and continue to store durable
  supporting evidence
- Audit, disable, and rollback paths are required before the loop can be
  considered production-ready

## Delivery Order

1. Connect feedback signals and evaluator scores into one decision engine
2. Auto-activate emerged skills and reload them across restarts
3. Add audit trail, disable, and rollback paths for automatic skill changes
4. Extend outcome-driven iteration from evidence-only updates to direct skill
   rewrites
5. Add rewrite guardrails so low-signal or noisy data cannot drift skills
6. Add end-to-end coverage for generate, activate, reload, rewrite, and rollback

## Validation Approach

- Unit tests for signal aggregation, lifecycle transitions, and rewrite guards
- Integration tests for emerged skill activation and restart reload
- Integration tests for outcome-driven skill rewrites that preserve evidence
- End-to-end tests that cover positive and negative feedback leading to keep,
  rewrite, disable, and rollback outcomes

## Rollback Notes

- Automatic skill rewrites must keep enough history to restore the prior active
  version without manual reconstruction
- Activation and reload logic must fail closed: a malformed emerged skill should
  not block the rest of the skill registry from loading
