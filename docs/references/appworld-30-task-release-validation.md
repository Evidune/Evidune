# AppWorld 30-Task Repeated Release Validation

- Date: 2026-07-16
- Release layer: V4 real corpus
- Corpus: `appworld-test_normal-66ad8099e121-c8c70c1136c4`
- Decision: do not promote the candidate
- Production canary: not started; retained as the later V5 release gate

## Executive Result

The full pinned 30-task AppWorld manifest was executed with a real Codex
`gpt-5.4` model. It contains 15 development tasks and 15 source-disjoint holdout
tasks. Each parent/candidate pair could use up to six attempts to obtain at least
three valid repetitions.

The candidate is not release-ready:

1. The development run had complete paired evidence on all 15 tasks, but the
   candidate produced 32 passes versus the parent's 36: a regression of four
   passes, or 8.9 percentage points over 45 valid repetitions per variant.
2. The holdout run was inconclusive because only 10 of 15 tasks obtained the
   required three valid repetitions for both variants.
3. On those 10 comparable holdout tasks, the candidate improved from 17/30 to
   25/30 passes, but the five incomplete tasks and an independent replay failure
   prevent that local uplift from authorizing promotion.
4. The candidate remains staged and unpromoted. No production shadow or canary
   traffic was exposed.

## Reproducibility and Provenance

| Item                        | Pinned value                                                                                                      |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Evidune repository commit   | `13d56cb20abc9625fe9c24839160e03028c59699`                                                                        |
| AppWorld package            | `0.1.3.post1`                                                                                                     |
| AppWorld source commit      | `66ad8099e12188ece0d3fe45e661dbc01880813b`                                                                        |
| Corpus manifest             | `examples/evaluation/appworld-test-normal-pilot.yaml`                                                             |
| Corpus digest               | `79f525de18884e36f6dfd0358c62b9b79ac9c0868b71b7b9ddbc260fa179eead`                                                |
| Adapter                     | `appworld` revision `v1`                                                                                          |
| Model                       | Codex `gpt-5.4`, temperature `0`                                                                                  |
| Parent                      | `1.0.0`, digest `49f955101b968edd85d390c2aa47192e319e42aa4d65ac68df9518428b39fe9a`                                |
| Candidate                   | `1.0.0-candidate-20260715123252250602`, digest `9a6d90a3ad37f6b56682531017df09e04f014bd8226dc3d594d1b32a32a8053e` |
| Candidate source experiment | `exp_94665e2d31a44989809640a76ab481b3`                                                                            |
| Repetitions                 | up to 6 attempts per task and variant; minimum 3 valid                                                            |
| Trial budgets               | 300 seconds total, 120 seconds per model call, 18 model turns, 30 tool calls                                      |

The candidate added stricter full-state reads, stable identifier normalization,
exact set/list delta checks, separate playback-transition verification, a final
state reread, and a rule to call `complete_task` only after all checks pass. The
30-task experiment measured whether those changes generalized beyond the
three-task candidate-generation pilot.

## Experiment Bundles

| Split       | Experiment                             | Status       | Trial envelope                    | Stored evidence                                                                   |
| ----------- | -------------------------------------- | ------------ | --------------------------------- | --------------------------------------------------------------------------------- |
| Development | `exp_1636f1d0fadc40c19d8428680c450643` | rejected     | 2026-07-16 13:48:05Z to 15:31:29Z | 7.7 MB under `.evidune/runtime/evaluations/exp_1636f1d0fadc40c19d8428680c450643/` |
| Holdout     | `exp_39baa4b7312c4b3a837e2fe9c9a7812a` | inconclusive | 2026-07-16 15:49:46Z to 18:27:17Z | 14 MB under `.evidune/runtime/evaluations/exp_39baa4b7312c4b3a837e2fe9c9a7812a/`  |

Each directory contains a pinned manifest, JSON and Markdown summaries, JUnit,
replay output, per-trial records, tool traces, and evaluator evidence. Runtime
artifacts remain local and ignored; this tracked document is their durable
release index and interpretation.

## Metrics Sought

The release experiment intentionally avoided one blended score. It examined:

- Task efficacy: authoritative AppWorld database-state pass/fail counts.
- Paired robustness: candidate-minus-parent pass deltas on the same task with at
  least three valid repetitions for each variant.
- Evidence completeness: fully paired tasks, valid/invalid counts, and whether
  missing repetitions made the decision inconclusive.
- Reliability: provider failures, budget exhaustion, timeout behavior, and safe
  restart/resume behavior.
- Operational efficiency: valid-run tool-call counts and which execution budget
  was exhausted.
- Correctness and collateral safety: exact final-state evaluation, not model
  self-judgment or textual completion claims.
- Reproducibility: pinned corpus/model/Skill digests plus independent replay of
  stored evidence.

## Split-Level Results

| Metric                        |                           Development |                                               Holdout |
| ----------------------------- | ------------------------------------: | ----------------------------------------------------: |
| Distinct tasks                |                                    15 |                                                    15 |
| Stored trial records          |                                   104 |                                                   129 |
| Valid trials                  |                                    90 |                                                    79 |
| Invalid trials                |                                    14 |                                                    50 |
| Fully paired tasks            |                                 15/15 |                                                 10/15 |
| Parent valid / pass / fail    |                           45 / 36 / 9 |                                          38 / 24 / 14 |
| Candidate valid / pass / fail |                          45 / 32 / 13 |                                           41 / 36 / 5 |
| Clean paired comparison       |                   candidate -4 passes |            candidate +8 passes on 10 comparable tasks |
| Task wins / losses / ties     |                             1 / 5 / 9 |                         5 / 1 / 4 on comparable tasks |
| Governance                    | fail: authoritative evaluator failure | inconclusive: minimum valid paired trials not reached |
| Independent replay            |                                  fail |                                                  fail |

The holdout's overall 24/38 parent and 36/41 candidate pass counts have different
task coverage and denominators. They are operational totals, not a valid paired
effect estimate. The defensible holdout comparison is limited to the 10 fully
paired tasks: parent 17/30 (56.7%) versus candidate 25/30 (83.3%), a gain of 8
passes or 26.7 percentage points.

Across both splits, the system stored 233 trial records: 169 valid and 64
invalid. Sixty-two invalid trials were budget exhaustion and two were external
connection errors.

## Task-Level Development Evidence

`P/F/I` means pass, fail, and invalid trial counts. Delta is candidate passes
minus parent passes.

| Task        | Parent P/F/I | Candidate P/F/I | Pass delta |
| ----------- | -----------: | --------------: | ---------: |
| `3d9a636_1` |        2/1/1 |           2/1/2 |          0 |
| `3d9a636_2` |        1/2/0 |           3/0/0 |         +2 |
| `3d9a636_3` |        3/0/1 |           2/1/0 |         -1 |
| `fd1f8fa_1` |        3/0/1 |           3/0/0 |          0 |
| `fd1f8fa_2` |        3/0/0 |           3/0/2 |          0 |
| `fd1f8fa_3` |        3/0/2 |           3/0/2 |          0 |
| `325d6ec_1` |        3/0/0 |           3/0/0 |          0 |
| `325d6ec_2` |        3/0/0 |           2/1/0 |         -1 |
| `325d6ec_3` |        3/0/0 |           3/0/0 |          0 |
| `29a7b7e_1` |        3/0/1 |           3/0/1 |          0 |
| `29a7b7e_2` |        3/0/0 |           3/0/0 |          0 |
| `29a7b7e_3` |        3/0/1 |           2/1/0 |         -1 |
| `21abae1_1` |        1/2/0 |           0/3/0 |         -1 |
| `21abae1_2` |        2/1/0 |           0/3/0 |         -2 |
| `21abae1_3` |        0/3/0 |           0/3/0 |          0 |

The regression is concentrated in five tasks, especially `21abae1_2`. This is
enough to reject the candidate even though one task, `3d9a636_2`, improved.

## Task-Level Holdout Evidence

`n/a` means one or both variants did not reach three valid repetitions, so the
task is excluded from the paired effect estimate.

| Task        | Parent P/F/I | Candidate P/F/I | Pass delta |
| ----------- | -----------: | --------------: | ---------: |
| `634f342_1` |        1/0/5 |           3/0/3 |        n/a |
| `634f342_2` |        3/0/3 |           3/0/2 |          0 |
| `634f342_3` |        2/1/0 |           2/0/4 |        n/a |
| `8749218_1` |        2/1/2 |           3/0/1 |         +1 |
| `8749218_2` |        1/2/1 |           3/0/0 |         +2 |
| `8749218_3` |        2/1/1 |           3/0/1 |         +1 |
| `2d9f728_1` |        1/2/0 |           0/3/0 |         -1 |
| `2d9f728_2` |        1/2/1 |           1/2/0 |          0 |
| `2d9f728_3` |        0/3/0 |           3/0/0 |         +3 |
| `6f4b9a5_1` |        1/0/5 |           3/0/3 |        n/a |
| `6f4b9a5_2` |        2/0/4 |           2/0/4 |        n/a |
| `6f4b9a5_3` |        1/0/5 |           1/0/5 |        n/a |
| `d6ac34d_1` |        3/0/0 |           3/0/0 |          0 |
| `d6ac34d_2` |        3/0/0 |           3/0/0 |          0 |
| `d6ac34d_3` |        1/2/0 |           3/0/0 |         +2 |

The run summary specifically lacked the required candidate evidence for
`634f342_3`, `6f4b9a5_2`, and `6f4b9a5_3`. Two additional tasks lacked a
complete parent/candidate pair. Replay still found authoritative candidate
failures and returned `promotable: false`.

## Reliability and Budget Findings

- Development invalid rate: 14/104 (13.5%). Twelve trials exhausted execution
  budgets and two encountered external `ConnectError` failures.
- Holdout invalid rate: 50/129 (38.8%). All 50 were budget exhaustion.
- Of the 62 budget-invalid trials across both splits, 45 exhausted the 18-turn
  model budget and 17 exceeded the 30-call tool budget.
- On valid development executions, mean tool calls were 13.40 for the parent
  and 12.76 for the candidate.
- On valid holdout executions, mean tool calls were 16.63 for the parent and
  16.27 for the candidate.

The candidate did not generally consume more tools than the parent. The evidence
instead shows task-specific long-horizon pressure shared by both variants,
especially on the holdout source groups. A future release run should calibrate
model-turn and tool-call headroom or simplify the Skill's search/verification
path before merely increasing total wall time.

## Improvements Formed

The experiment produced improvements at two levels.

### Candidate Skill lessons

- Full-state reads and exact delta checks materially helped five comparable
  holdout tasks.
- The same stricter procedure regressed five development tasks, so the added
  verification is not yet selective enough.
- A next candidate should retain complete final-state verification while
  reducing unnecessary or brittle intermediate work on the regressed task
  families.

### Evaluation harness improvements

- Development and holdout fail-fast behavior is now independently configurable,
  allowing a release experiment to collect the complete failure distribution.
- An interrupted experiment can resume the same immutable experiment id,
  reconstruct stored counts, and skip completed task/variant/trial rows.
- Model calls now have a hard wall-clock boundary even when a provider stream
  resists asyncio cancellation.
- The outer trial deadline also returns promptly when nested work ignores
  cancellation.
- Provider and budget failures remain typed invalid evidence rather than being
  counted as Skill failures.
- The manifest now allows six attempts to obtain three valid repetitions,
  exposing where retry headroom is still insufficient.

These changes were required by real failures encountered during the formal
batch, not added speculatively.

## Next Release Gate

The next candidate must:

1. address the five development regressions and rerun the V4 corpus;
2. obtain at least three valid repetitions for both variants on all 15 holdout
   tasks, not only the easier comparable subset;
3. pass authoritative replay with no candidate state failures;
4. preserve pinned source, model, corpus, budgets, and immutable report
   artifacts; and
5. only after V4 passes, enter a bounded production V5 shadow/canary with an
   explicit rollback policy and delayed-outcome observation window.

Production canary therefore remains a future release validation step, not an
unfinished part of this rejected V4 candidate.
