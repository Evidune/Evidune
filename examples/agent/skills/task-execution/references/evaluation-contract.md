# task-execution Evaluation Contract

## Success Criteria

- The response completes the user's operational outcome.
- Claims are grounded in tool output, user-provided data, or explicit uncertainty.
- Reusable lessons are captured in memory, references, or a skill transaction when appropriate.

## Observable Signals

- Tool trace shows relevant inspection, validation, or a clear explanation that tools were unavailable.
- Execution metadata and user feedback support whether the task was completed.

## Failure Modes

- Skipped required verification.
- Hallucinated current external state.
- Failed to capture an obviously reusable workflow.
