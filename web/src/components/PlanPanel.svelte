<script lang="ts">
  import type { ConversationMode, ConversationPlan } from '../lib/types'

  let {
    mode,
    plan,
  }: {
    mode: ConversationMode
    plan: ConversationPlan | null
  } = $props()

  const hasPlan = $derived(!!plan && plan.items.length > 0)
</script>

<section class="panel">
  <div class="panel-header">
    <div>
      <div class="eyebrow">Current Plan</div>
      <h2>{mode === 'plan' ? 'Planning' : 'Execution Progress'}</h2>
    </div>
    <span class="mode-badge" class:plan={mode === 'plan'}>
      {mode === 'plan' ? 'PLAN' : 'EXECUTE'}
    </span>
  </div>

  {#if hasPlan}
    {#if plan?.explanation}
      <p class="explanation">{plan.explanation}</p>
    {/if}

    <ol class="steps">
      {#each plan?.items ?? [] as item, index (`${index}-${item.step}`)}
        <li class="step">
          <span class="status" class:pending={item.status === 'pending'} class:progress={item.status === 'in_progress'} class:done={item.status === 'completed'}>
            {item.status}
          </span>
          <span class="step-text">{item.step}</span>
        </li>
      {/each}
    </ol>
  {:else}
    <p class="empty">
      {mode === 'plan'
        ? 'No structured plan yet. The next planning turn can create one.'
        : 'No structured plan is attached to this conversation yet.'}
    </p>
  {/if}
</section>

<style>
  .panel {
    margin: 16px 20px 0;
    padding: 16px;
    border: 1px solid var(--border);
    border-radius: 14px;
    background:
      linear-gradient(135deg, rgba(99, 102, 241, 0.08), rgba(14, 165, 233, 0.04)),
      var(--surface);
  }

  .panel-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
  }

  .eyebrow {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text2);
  }

  h2 {
    margin: 4px 0 0;
    font-size: 15px;
    font-weight: 600;
  }

  .mode-badge {
    border-radius: 999px;
    padding: 5px 10px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: #1d4ed8;
    background: rgba(59, 130, 246, 0.12);
  }

  .mode-badge.plan {
    color: #7c3aed;
    background: rgba(124, 58, 237, 0.14);
  }

  .explanation {
    margin: 12px 0 0;
    color: var(--text2);
    font-size: 13px;
    line-height: 1.6;
  }

  .steps {
    list-style: none;
    margin: 14px 0 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .step {
    display: flex;
    align-items: flex-start;
    gap: 10px;
  }

  .status {
    min-width: 88px;
    border-radius: 999px;
    padding: 4px 8px;
    text-align: center;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    background: rgba(148, 163, 184, 0.15);
    color: #94a3b8;
  }

  .status.pending {
    background: rgba(148, 163, 184, 0.15);
    color: #94a3b8;
  }

  .status.progress {
    background: rgba(245, 158, 11, 0.15);
    color: #f59e0b;
  }

  .status.done {
    background: rgba(34, 197, 94, 0.15);
    color: #22c55e;
  }

  .step-text {
    flex: 1;
    font-size: 13px;
    line-height: 1.55;
  }

  .empty {
    margin: 12px 0 0;
    font-size: 13px;
    color: var(--text2);
  }

  @media (max-width: 640px) {
    .panel {
      margin: 12px 12px 0;
    }

    .step {
      flex-direction: column;
      gap: 6px;
    }
  }
</style>
