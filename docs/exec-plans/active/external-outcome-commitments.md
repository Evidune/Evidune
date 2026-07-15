# External Outcome Commitments

- Title: Generalized external outcome evaluation via commitments and probes
- Driving area: `core/`, `agent/`, `memory/`, `skills/`, `adapters/`
- Status: active

## Goal

Let skill evaluation consume delayed, external ground truth — deploy stability,
market performance of a recommendation, engagement on a published post — through
one generalized mechanism instead of per-scenario integration code:

- every externally-verifiable skill execution records a commitment
- probes observe the world on a schedule and write normalized observations
- deterministic scorers turn matured observations into outcome evidence
- existing decision rails (KPI windows, iteration harness, observation window)
  consume that evidence unchanged

## Core Abstraction

All external evaluation reduces to one four-tuple:

| Part       | Meaning                                                         |
| ---------- | --------------------------------------------------------------- |
| Commitment | what this execution left in the world and what "good" means     |
| Probe      | how to observe the relevant world state (declarative tool call) |
| Schedule   | when observations are meaningful (maturity, multiple horizons)  |
| Scorer     | matured observations -> score / verdict                         |

Examples: a deploy commits to "error rate stable and not rolled back after 1h";
a stock recommendation commits to "positive excess return at T+5"; a published
post commits to "engagement at 72h not below account baseline".

## Current State

- `core/metrics.py` already defines `OutcomeObservation` and the
  `MetricsAdapter` registry; `generic_csv` is the only adapter
- outcome KPI windows, decision packets, and the post-rewrite observation
  window already consume outcome data end to end
- nothing links a serve-mode execution to the external entity it produced
- no maturation semantics: observations enter windows regardless of age
- `OutcomeContract` assumes higher-is-better KPIs

## Decisions

- The commitment ledger and decision wiring stay in Evidune. This loop is the
  product differentiator; external platforms are evidence sources or sinks,
  never the decision brain.
- Probes are declarative (tool + args + extract), executed by a generic loop
  reusing the existing agent tool surface — not one Python adapter per
  scenario. `MetricsAdapter` remains supported as one probe type.
- Scoring predicates run in a sandboxed expression language (CEL via
  `cel-python`, or `json-logic`) — never `eval()` or raw Python. Contracts are
  LLM-discoverable and LLM-rewritable, so the scoring expression is a security
  boundary, not a style choice.
- Field extraction uses `jmespath` (or `jsonpath-ng`). New dependencies land
  as optional extras in `pyproject`, never in the core install path.
- Probe failure is never negative evidence. A failed probe marks the binding
  for retry; it must not write a low score (mirror of the "unparseable judge
  output is invalid, not 0.0" rule).
- Commitments are explicit first: declared in SKILL.md frontmatter or via a
  `record_outcome_binding` tool call. LLM-inferred commitments (mirroring
  `discover_contract`) are phase 2 and must pass deterministic validation —
  the entity id must appear in the tool trace — before entering the ledger.
- Observation-window and KPI-window judgments should adopt experiment-grade
  statistics (minimum sample size, sequential-test style thresholds, borrowed
  from GrowthBook's documented stats engine) to replace the current
  sample-count-ratio confidence.
- Optional export of evidence to an LLM observability sidecar (e.g. Langfuse
  self-hosted) is out of scope for phase 1 and never a decision dependency.

## Delivery Order

1. `outcome_bindings` ledger table + store API with lifecycle
   `committed -> observing -> matured -> scored` (memory/)
2. Explicit binding entry points: SKILL.md frontmatter template plus a
   `record_outcome_binding` agent tool (skills/, agent/tools/)
3. Generic probe executor in the `evidune run` loop: due bindings -> tool
   call -> jmespath extract -> `OutcomeObservation` (core/)
4. Sandboxed predicate scorer + `direction` support in `OutcomeContract`
   so lower-is-better KPIs (deploy duration) work (skills/, core/analyzer.py)
5. Maturity filtering and multi-horizon metrics in analyzer windows
   (`excess_return_5d` / `_20d` naming convention)
6. Statistics upgrade for window judgments (analyzer + iteration observation)
7. Phase 2: LLM commitment inference with tool-trace validation; LLM judge
   fallback for observations no predicate can score

## Validation Approach

- Unit tests: ledger lifecycle transitions, probe extract mapping, predicate
  scorer sandboxing (hostile expressions must not execute), maturity filters
- Integration tests: end-to-end commitment -> probe -> observation -> KPI
  window -> harness decision using a stub HTTP tool
- Regression tests: probe failure never produces a score; immature
  observations never enter windows
- Existing suites must stay green: the ledger feeds `OutcomeObservation`,
  so downstream analyzer/harness behavior is already covered

## Rollback Notes

- The ledger is additive: disabling the probe executor returns the system to
  today's behavior (CSV adapter ingestion only) with no schema rollback needed
- Scored commitments write standard `OutcomeObservation` rows; removing the
  feature strands no decision state
- Keep the predicate language behind one scorer interface so the expression
  engine can be swapped without touching contracts
