<script lang="ts">
  let { message, kind = 'info', onClose }: {
    message: string
    kind?: 'info' | 'success' | 'error'
    onClose?: () => void
  } = $props()
</script>

<div class="toast" class:success={kind === 'success'} class:error={kind === 'error'}>
  <span class="icon">
    {#if kind === 'success'}✨{:else if kind === 'error'}⚠{:else}ℹ{/if}
  </span>
  <span class="msg">{message}</span>
  {#if onClose}
    <button class="close" onclick={onClose} aria-label="Close">×</button>
  {/if}
</div>

<style>
  .toast {
    position: fixed;
    top: 70px;
    left: 50%;
    transform: translateX(-50%);
    z-index: 1000;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 16px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    font-size: 13px;
    color: var(--text);
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
    animation: slideDown 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
    max-width: 90vw;
  }

  @keyframes slideDown {
    from {
      opacity: 0;
      transform: translateX(-50%) translateY(-12px);
    }
    to {
      opacity: 1;
      transform: translateX(-50%) translateY(0);
    }
  }

  .toast.success {
    border-color: var(--accent);
    background: linear-gradient(180deg, rgba(99, 102, 241, 0.15), var(--surface2));
  }

  .toast.error {
    border-color: #ef4444;
  }

  .icon {
    font-size: 16px;
  }

  .msg {
    flex: 1;
  }

  .close {
    background: none;
    border: none;
    color: var(--text2);
    font-size: 18px;
    cursor: pointer;
    padding: 0 4px;
    line-height: 1;
  }

  .close:hover {
    color: var(--text);
  }
</style>
