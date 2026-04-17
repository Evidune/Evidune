<script lang="ts">
  import type { BudgetSummary, ConvergenceSummary, TaskEvent } from '../lib/types'

  let {
    squad,
    status,
    environmentId,
    environmentStatus,
    events,
    convergence,
    budget,
  }: {
    squad?: string | null
    status?: string | null
    environmentId?: string | null
    environmentStatus?: string | null
    events?: TaskEvent[]
    convergence?: ConvergenceSummary | null
    budget?: BudgetSummary | null
  } = $props()
</script>

{#if events && events.length > 0}
  <section class="timeline" data-testid="task-timeline">
    <div class="timeline-header">
      <div class="eyebrow">Swarm Task</div>
      <div class="meta">
        {#if squad}<span class="pill" data-testid="task-timeline-squad">{squad}</span>{/if}
        {#if status}<span class="pill status" data-testid="task-timeline-status">{status}</span>{/if}
        {#if environmentStatus}
          <span class="pill" data-testid="task-timeline-environment">{environmentStatus}</span>
        {/if}
        {#if convergence?.decision}
          <span class="pill" data-testid="task-timeline-decision">{convergence.decision}</span>
        {/if}
      </div>
    </div>
    <div class="events">
      {#each events as event (`${event.sequence}-${event.type}`)}
        <div class="event" data-testid="task-event">
          <div class="event-head">
            <span class="seq">#{event.sequence}</span>
            {#if event.phase}<span class="phase">{event.phase}</span>{/if}
            {#if event.role}<span class="role">{event.role}</span>{/if}
          </div>
          <div class="msg">{event.message}</div>
        </div>
      {/each}
    </div>
    {#if budget}
      <div class="budget" data-testid="task-budget">
        {#if environmentId}env {environmentId} · {/if}
        rounds {budget.rounds_used ?? 0}/{budget.max_rounds ?? 0}
        · tools {budget.tool_calls_used ?? 0}/{budget.tool_call_budget ?? 0}
        · tokens ~{budget.token_used ?? 0}/{budget.token_budget ?? 0}
      </div>
    {/if}
  </section>
{/if}

<style>
  .timeline {
    margin-bottom: 10px;
    padding: 10px 12px;
    border-radius: 12px;
    border: 1px solid var(--border);
    background: rgba(14, 165, 233, 0.06);
  }

  .timeline-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 8px;
  }

  .eyebrow {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text2);
    font-weight: 700;
  }

  .meta {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }

  .pill {
    border-radius: 999px;
    padding: 3px 8px;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    background: rgba(37, 99, 235, 0.12);
    color: #2563eb;
  }

  .pill.status {
    background: rgba(16, 185, 129, 0.12);
    color: #10b981;
  }

  .events {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .event {
    padding-left: 10px;
    border-left: 2px solid rgba(37, 99, 235, 0.35);
  }

  .event-head {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 2px;
    flex-wrap: wrap;
  }

  .seq,
  .phase,
  .role {
    font-size: 10px;
    color: var(--text2);
    font-family: var(--mono);
  }

  .msg {
    font-size: 12px;
    line-height: 1.5;
  }

  .budget {
    margin-top: 10px;
    font-size: 11px;
    color: var(--text2);
  }
</style>
