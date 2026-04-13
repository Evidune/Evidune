<script lang="ts">
  import type { Message } from '../lib/types'

  let { message }: { message: Message } = $props()

  const isUser = $derived(message.role === 'user')
</script>

<div class="msg" class:user={isUser} class:bot={!isUser}>
  <span class="msg-label">{isUser ? 'You' : 'Aiflay'}</span>
  <div class="msg-body">{message.content}</div>
</div>

<style>
  .msg {
    display: flex;
    flex-direction: column;
    max-width: 80%;
    animation: fadeIn 0.3s ease;
  }

  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .msg.user { align-self: flex-end; }
  .msg.bot { align-self: flex-start; }

  .msg-label {
    font-size: 11px;
    color: var(--text2);
    margin-bottom: 4px;
    padding: 0 4px;
  }

  .msg.user .msg-label { text-align: right; }

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

  @media (max-width: 640px) {
    .msg { max-width: 92%; }
  }
</style>
