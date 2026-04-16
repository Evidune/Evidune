<script lang="ts">
  import type { ConversationSummary } from '../lib/types'

  let {
    conversations,
    activeId,
    onSelect,
    onNew,
    onDelete,
    onArchive,
  }: {
    conversations: ConversationSummary[]
    activeId: string
    onSelect: (id: string) => void
    onNew: () => void
    onDelete: (id: string) => void
    onArchive: (id: string) => void
  } = $props()

  let menuOpenId: string | null = $state(null)

  function fmtWhen(iso: string): string {
    try {
      const d = new Date(iso)
      const now = new Date()
      const sameDay = d.toDateString() === now.toDateString()
      if (sameDay) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
    } catch {
      return ''
    }
  }

  function toggleMenu(id: string, event: MouseEvent) {
    event.stopPropagation()
    menuOpenId = menuOpenId === id ? null : id
  }

  function close() {
    menuOpenId = null
  }

  function handleDelete(id: string, event: MouseEvent) {
    event.stopPropagation()
    close()
    if (confirm('Delete this conversation? This cannot be undone.')) {
      onDelete(id)
    }
  }

  function handleArchive(id: string, event: MouseEvent) {
    event.stopPropagation()
    close()
    onArchive(id)
  }
</script>

<svelte:window onclick={close} />

<aside class="sidebar">
  <div class="header">
    <span class="title">Conversations</span>
    <button class="new-btn" title="New conversation" onclick={onNew}>+ New</button>
  </div>

  <div class="list">
    {#if conversations.length === 0}
      <p class="empty">No conversations yet</p>
    {/if}
    {#each conversations as c (c.id)}
      <div
        class="item"
        class:active={c.id === activeId}
        role="button"
        tabindex="0"
        onclick={() => onSelect(c.id)}
        onkeydown={e => e.key === 'Enter' && onSelect(c.id)}
      >
        <div class="row-top">
          <div class="label-group">
            <span class="label">{c.title || 'Untitled'}</span>
            {#if c.mode === 'plan'}
              <span class="badge mode">plan</span>
            {/if}
            {#if c.has_plan}
              <span class="badge plan">plan</span>
            {/if}
          </div>
          <span class="when">{fmtWhen(c.updated_at)}</span>
        </div>
        {#if c.preview}
          <div class="preview">{c.preview}</div>
        {/if}
        <button
          class="more"
          aria-label="Actions"
          onclick={e => toggleMenu(c.id, e)}
        >⋯</button>
        {#if menuOpenId === c.id}
          <div
            class="menu"
            onclick={e => e.stopPropagation()}
            onkeydown={e => e.stopPropagation()}
            role="menu"
            tabindex="-1"
          >
            <button onclick={e => handleArchive(c.id, e)}>Archive</button>
            <button class="danger" onclick={e => handleDelete(c.id, e)}>Delete</button>
          </div>
        {/if}
      </div>
    {/each}
  </div>
</aside>

<style>
  .sidebar {
    width: 260px;
    background: var(--surface);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
    overflow: hidden;
  }

  .header {
    padding: 14px 16px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .title {
    font-size: 13px;
    font-weight: 600;
    color: var(--text2);
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  .new-btn {
    background: var(--accent);
    color: white;
    border: none;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
    cursor: pointer;
    transition: background 0.15s;
  }

  .new-btn:hover {
    background: var(--accent2);
  }

  .list {
    flex: 1;
    overflow-y: auto;
    padding: 6px;
  }

  .empty {
    padding: 16px;
    font-size: 12px;
    color: var(--text2);
    text-align: center;
  }

  .item {
    position: relative;
    padding: 10px 12px;
    border-radius: 8px;
    cursor: pointer;
    margin-bottom: 2px;
    border: 1px solid transparent;
    transition: all 0.15s;
  }

  .item:hover {
    background: var(--surface2);
  }

  .item.active {
    background: var(--surface2);
    border-color: var(--accent);
  }

  .row-top {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 8px;
  }

  .label-group {
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
    flex: 1;
  }

  .label {
    font-size: 13px;
    font-weight: 500;
    color: var(--text);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
  }

  .badge {
    flex-shrink: 0;
    border-radius: 999px;
    padding: 2px 6px;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .badge.mode {
    color: #7c3aed;
    background: rgba(124, 58, 237, 0.14);
  }

  .badge.plan {
    color: #2563eb;
    background: rgba(37, 99, 235, 0.14);
  }

  .when {
    font-size: 11px;
    color: var(--text2);
    flex-shrink: 0;
  }

  .preview {
    font-size: 11px;
    color: var(--text2);
    margin-top: 3px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .more {
    position: absolute;
    top: 6px;
    right: 6px;
    background: transparent;
    border: none;
    color: var(--text2);
    cursor: pointer;
    font-size: 16px;
    opacity: 0;
    transition: opacity 0.15s;
    padding: 0 6px;
    line-height: 1;
  }

  .item:hover .more,
  .item.active .more {
    opacity: 1;
  }

  .more:hover {
    color: var(--text);
  }

  .menu {
    position: absolute;
    top: 28px;
    right: 6px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    z-index: 10;
    min-width: 120px;
    box-shadow: 0 6px 18px rgba(0, 0, 0, 0.4);
  }

  .menu button {
    display: block;
    width: 100%;
    text-align: left;
    background: transparent;
    border: none;
    color: var(--text);
    padding: 8px 12px;
    font-size: 13px;
    cursor: pointer;
  }

  .menu button:hover {
    background: rgba(99, 102, 241, 0.1);
  }

  .menu button.danger:hover {
    color: #ef4444;
  }

  @media (max-width: 640px) {
    .sidebar {
      width: 100%;
      max-height: 200px;
      border-right: none;
      border-bottom: 1px solid var(--border);
    }
  }
</style>
