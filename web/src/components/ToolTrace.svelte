<script lang="ts">
  import type { ToolTraceEntry } from '../lib/types'

  let { trace }: { trace: ToolTraceEntry[] } = $props()
  let expanded = $state<Record<number, boolean>>({})

  function toggle(i: number) {
    expanded[i] = !expanded[i]
  }

  function argsPreview(args: Record<string, unknown>): string {
    const parts = Object.entries(args).map(([k, v]) => {
      const s = typeof v === 'string' ? v : JSON.stringify(v)
      return `${k}=${s.length > 40 ? s.slice(0, 40) + '…' : s}`
    })
    return parts.join(', ')
  }
</script>

{#if trace && trace.length > 0}
  <div class="tool-trace">
    {#each trace as t, i}
      <div class="entry" class:error={t.is_error}>
        <button class="head" onclick={() => toggle(i)}>
          <span class="icon">{t.is_error ? '⚠' : '🔧'}</span>
          {#if t.role}<span class="role">{t.role}</span>{/if}
          <span class="name">{t.name}</span>
          <span class="args-preview">{argsPreview(t.arguments)}</span>
          <span class="chev">{expanded[i] ? '▾' : '▸'}</span>
        </button>
        {#if expanded[i]}
          <div class="body">
            <div class="section">
              <div class="section-label">Arguments</div>
              <pre>{JSON.stringify(t.arguments, null, 2)}</pre>
            </div>
            <div class="section">
              <div class="section-label">Result</div>
              <pre>{t.result}</pre>
            </div>
          </div>
        {/if}
      </div>
    {/each}
  </div>
{/if}

<style>
  .tool-trace {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin: 6px 0;
    max-width: 100%;
  }

  .entry {
    border: 1px solid var(--border);
    border-radius: 8px;
    background: rgba(99, 102, 241, 0.05);
    overflow: hidden;
  }

  .entry.error {
    background: rgba(239, 68, 68, 0.08);
    border-color: rgba(239, 68, 68, 0.3);
  }

  .head {
    width: 100%;
    background: transparent;
    border: none;
    color: var(--text);
    padding: 6px 10px;
    font-size: 12px;
    font-family: var(--mono);
    text-align: left;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .head:hover {
    background: rgba(99, 102, 241, 0.08);
  }

  .icon {
    font-size: 12px;
  }

  .name {
    font-weight: 600;
    color: var(--accent2);
  }

  .role {
    font-size: 10px;
    color: var(--text2);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .entry.error .name {
    color: #f87171;
  }

  .args-preview {
    color: var(--text2);
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .chev {
    color: var(--text2);
    font-size: 10px;
  }

  .body {
    padding: 8px 10px;
    border-top: 1px solid var(--border);
    font-family: var(--mono);
    font-size: 11px;
  }

  .section {
    margin-bottom: 6px;
  }

  .section:last-child {
    margin-bottom: 0;
  }

  .section-label {
    color: var(--text2);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 3px;
  }

  pre {
    margin: 0;
    padding: 6px 8px;
    background: rgba(10, 10, 15, 0.6);
    border: 1px solid var(--border);
    border-radius: 4px;
    overflow-x: auto;
    max-height: 240px;
    overflow-y: auto;
    color: var(--text);
    white-space: pre-wrap;
    word-break: break-word;
  }
</style>
