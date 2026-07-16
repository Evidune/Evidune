# Conversation Context

Evidune preserves the complete conversation transcript in SQLite while
assembling a bounded prompt context for each `serve` turn.

## Behavior

- User and assistant messages are append-only during normal agent operation.
  The runtime does not automatically trim old messages.
- Every message has a stable SQLite row id. Graph-memory message keys use that
  id instead of a position in a moving history window.
- The prompt receives a contiguous recent-message suffix selected by estimated
  token cost, not by a fixed message count.
- Messages that move outside the recent suffix are folded into a persisted
  rolling summary. The summary records the last covered message id and source
  message count, so later turns only summarize newly displaced messages.
- Summary failures do not delete or mark pending transcript rows as covered.
  The full transcript remains available and summarization retries on a later
  turn.
- Compact tool observations are stored separately from the transcript and
  included in later prompts within their own token budget.

The default budgets are:

```yaml
agent:
  context:
    recent_token_budget: 20000
    summary_token_budget: 3000
    tool_observation_token_budget: 2000
```

The estimator counts CJK characters directly and approximates other text at
four characters per token. It is intentionally deterministic and conservative;
provider tokenizers remain the final authority.

## Prompt Assembly

The single-agent prompt is assembled from:

1. identity, operating mode, and selected skills
2. persisted conversation summary
3. graph-reconstructed memory or fact fallback
4. recent compact tool observations
5. token-budgeted recent transcript
6. current user message

The swarm harness receives the same summary, recent transcript, and tool
observations through its task brief. Graph memory keeps its independent
`memory.graph.max_context_items` evidence cap; the default remains `12`.

## Diagnostics

Each turn persists a context report with:

- configured budgets
- full transcript count and estimated size
- summary coverage
- recent-message ids and estimated tokens
- selected tool-observation count
- selected skills and graph evidence
- assembled message and tool-schema estimates

The report is available through:

- the `/context detail` conversation command, which does not add diagnostic
  request/response rows to the transcript
- the read-only `context_detail` internal tool
- `GET /api/conversations/{conversation_id}/context`
- `OutboundMessage.metadata.context_detail`

Conversation history remains available through
`GET /api/conversations/{conversation_id}/history`, which returns the complete
stored transcript.

## Storage

`MemoryStore` owns:

- `messages`: complete transcript with stable ids
- `conversation_summaries`: rolling summary and coverage watermark
- `tool_observations`: compact durable tool results
- `conversation_context_reports`: last assembled context diagnostic

Explicit conversation deletion removes these rows and associated graph-memory
message/tool nodes. No vector database is required.
