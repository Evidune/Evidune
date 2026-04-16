<script lang="ts">
  import type { ConversationMode } from '../lib/types'

  let {
    mode,
    disabled = false,
    onChange,
  }: {
    mode: ConversationMode
    disabled?: boolean
    onChange: (mode: ConversationMode) => void
  } = $props()
</script>

<div class="toggle" role="tablist" aria-label="Conversation mode" data-testid="mode-toggle">
  <button
    data-testid="mode-plan"
    class="pill"
    class:active={mode === 'plan'}
    role="tab"
    aria-selected={mode === 'plan'}
    onclick={() => onChange('plan')}
    {disabled}
  >
    Plan
  </button>
  <button
    data-testid="mode-execute"
    class="pill"
    class:active={mode === 'execute'}
    role="tab"
    aria-selected={mode === 'execute'}
    onclick={() => onChange('execute')}
    {disabled}
  >
    Execute
  </button>
</div>

<style>
  .toggle {
    display: inline-flex;
    gap: 4px;
    padding: 4px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--surface2);
  }

  .pill {
    border: none;
    background: transparent;
    color: var(--text2);
    border-radius: 999px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
  }

  .pill.active {
    background: var(--accent);
    color: white;
  }

  .pill:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
</style>
