<div class="typing-wrap">
  <span class="msg-label">Evidune</span>
  <div class="typing">
    <span></span><span></span><span></span>
  </div>
</div>

<style>
  .typing-wrap {
    display: flex;
    flex-direction: column;
    align-self: flex-start;
    animation: fadeIn 0.2s ease;
  }

  @keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  /* .msg-label styles live in app.css */

  .typing {
    display: flex;
    gap: 5px;
    padding: 14px 18px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    border-bottom-left-radius: 4px;
  }

  .typing span {
    width: 7px;
    height: 7px;
    background: var(--text2);
    border-radius: 50%;
    animation: bounce 1.4s ease-in-out infinite;
  }

  .typing span:nth-child(2) { animation-delay: 0.16s; }
  .typing span:nth-child(3) { animation-delay: 0.32s; }

  @keyframes bounce {
    0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
    30% { transform: translateY(-5px); opacity: 1; }
  }
</style>
