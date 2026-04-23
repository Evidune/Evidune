<script lang="ts">
  import type { Message, SignalType } from '../lib/types'
  import { sendFeedback } from '../lib/api'
  import HarnessSummary from './HarnessSummary.svelte'
  import SkillCreationCard from './SkillCreationCard.svelte'
  import TaskTimeline from './TaskTimeline.svelte'
  import ToolTrace from './ToolTrace.svelte'

  let { message, onRegenerate }: { message: Message; onRegenerate?: () => void } = $props()

  const isUser = $derived(message.role === 'user')
  let feedback: Partial<Record<SignalType, boolean>> = $state({})
  let copiedJustNow = $state(false)

  $effect(() => {
    feedback = message.feedback ?? {}
  })

  async function send(signal: SignalType, value: boolean | number = true) {
    if (!message.executionIds || message.executionIds.length === 0) return
    feedback = { ...feedback, [signal]: !!value }
    // Send for each execution id (a turn may touch multiple skills)
    await Promise.all(
      message.executionIds.map(id =>
        sendFeedback({ execution_id: id, signal, value }),
      ),
    )
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(message.content)
      copiedJustNow = true
      setTimeout(() => (copiedJustNow = false), 1500)
      send('copied', true)
    } catch {
      // clipboard blocked — silent fail
    }
  }

  function thumbsUp() {
    if (feedback.thumbs_up) return
    send('thumbs_up', true)
  }

  function thumbsDown() {
    if (feedback.thumbs_down) return
    send('thumbs_down', true)
  }

  function regenerate() {
    send('regenerated', true)
    onRegenerate?.()
  }
</script>

<div
  class="msg"
  class:user={isUser}
  class:bot={!isUser}
  data-testid={isUser ? 'user-message' : 'assistant-message'}
>
  <span class="msg-label">{isUser ? 'You' : 'Evidune'}</span>

  {#if !isUser && message.toolTrace && message.toolTrace.length > 0}
    <ToolTrace trace={message.toolTrace} />
  {/if}

  {#if !isUser && message.taskEvents && message.taskEvents.length > 0}
    <TaskTimeline
      squad={message.squad}
      status={message.taskStatus}
      events={message.taskEvents}
      convergence={message.convergenceSummary}
      budget={message.budgetSummary}
      environmentId={message.environmentId}
      environmentStatus={message.environmentStatus}
    />
  {/if}

  {#if !isUser}
    <HarnessSummary
      environmentId={message.environmentId}
      environmentStatus={message.environmentStatus}
      validationSummary={message.validationSummary}
      deliverySummary={message.deliverySummary}
      artifactManifest={message.artifactManifest}
    />
  {/if}

  {#if !isUser && message.skillCreation}
    <SkillCreationCard creation={message.skillCreation} />
  {/if}

  <div
    class="msg-body"
    data-testid={isUser ? 'user-message-body' : 'assistant-message-body'}
  >{message.content}</div>

  {#if !isUser && message.executionIds && message.executionIds.length > 0}
    <div class="actions">
      <button
        class="action"
        class:active={feedback.thumbs_up}
        title="Good response"
        onclick={thumbsUp}
        data-testid="feedback-thumbs-up"
      >
        👍
      </button>
      <button
        class="action"
        class:active={feedback.thumbs_down}
        title="Bad response"
        onclick={thumbsDown}
        data-testid="feedback-thumbs-down"
      >
        👎
      </button>
      <button class="action" title={copiedJustNow ? 'Copied!' : 'Copy'} onclick={copy}>
        {copiedJustNow ? '✓' : '📋'}
      </button>
      {#if onRegenerate}
        <button class="action" title="Regenerate" onclick={regenerate}>🔄</button>
      {/if}
    </div>
  {/if}
</div>

<style>
  .msg {
    display: flex;
    flex-direction: column;
    max-width: 80%;
    animation: fadeIn 0.3s ease;
  }

  @keyframes fadeIn {
    from {
      opacity: 0;
      transform: translateY(8px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  .msg.user {
    align-self: flex-end;
  }
  .msg.bot {
    align-self: flex-start;
  }

  /* .msg-label base styles live in app.css; only the user-side
     alignment override stays here */
  .msg.user :global(.msg-label) {
    text-align: right;
  }

  .msg-body {
    padding: 12px 16px;
    border-radius: var(--radius);
    font-size: 14px;
    line-height: 1.7;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .msg.user .msg-body {
    background: var(--user-bg);
    border: 1px solid #312e81;
    border-bottom-right-radius: 4px;
  }

  .msg.bot .msg-body {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-bottom-left-radius: 4px;
  }

  .actions {
    display: flex;
    gap: 4px;
    margin-top: 6px;
    padding: 0 4px;
    opacity: 0;
    transition: opacity 0.2s;
  }

  .msg:hover .actions {
    opacity: 1;
  }

  .action {
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 13px;
    cursor: pointer;
    color: var(--text2);
    transition: all 0.15s;
  }

  .action:hover {
    background: var(--surface2);
    color: var(--text);
    border-color: var(--accent);
  }

  .action.active {
    background: rgba(99, 102, 241, 0.15);
    border-color: var(--accent);
    color: var(--accent2);
  }

  @media (max-width: 640px) {
    .msg {
      max-width: 92%;
    }
    .actions {
      opacity: 1; /* Always visible on mobile (no hover) */
    }
  }
</style>
