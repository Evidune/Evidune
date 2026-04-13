<script lang="ts">
  let { onSend, disabled = false }: { onSend: (text: string) => void; disabled?: boolean } = $props()
  let text = $state('')
  let textarea: HTMLTextAreaElement

  function handleSend() {
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    text = ''
    if (textarea) textarea.style.height = 'auto'
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  function handleInput() {
    if (textarea) {
      textarea.style.height = 'auto'
      textarea.style.height = Math.min(textarea.scrollHeight, 160) + 'px'
    }
  }
</script>

<div class="input-area">
  <div class="input-row">
    <textarea
      bind:this={textarea}
      bind:value={text}
      onkeydown={handleKeydown}
      oninput={handleInput}
      rows={1}
      placeholder="Type a message..."
      {disabled}
    ></textarea>
    <button onclick={handleSend} disabled={disabled || !text.trim()}>
      Send
    </button>
  </div>
  <div class="input-hint">Enter to send, Shift+Enter for new line</div>
</div>

<style>
  .input-area {
    padding: 16px 20px;
    border-top: 1px solid var(--border);
    background: var(--surface);
    flex-shrink: 0;
  }

  .input-row {
    display: flex;
    gap: 10px;
    align-items: flex-end;
  }

  textarea {
    flex: 1;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    color: var(--text);
    font-family: var(--font);
    font-size: 14px;
    padding: 12px 16px;
    resize: none;
    min-height: 44px;
    max-height: 160px;
    outline: none;
    transition: border-color 0.2s;
  }

  textarea:focus { border-color: var(--accent); }
  textarea::placeholder { color: var(--text2); }

  button {
    background: var(--accent);
    color: white;
    border: none;
    border-radius: var(--radius);
    padding: 12px 20px;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s;
    white-space: nowrap;
  }

  button:hover:not(:disabled) { background: var(--accent2); }
  button:disabled { opacity: 0.35; cursor: not-allowed; }

  .input-hint {
    font-size: 11px;
    color: var(--text2);
    margin-top: 6px;
    padding: 0 4px;
  }
</style>
