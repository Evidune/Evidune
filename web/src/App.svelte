<script lang="ts">
  import { onMount, tick } from 'svelte'
  import { messages, skills, activeSkills, isLoading, conversationId } from './lib/stores'
  import { fetchSkills, sendMessage } from './lib/api'
  import type { Message } from './lib/types'
  import ChatMessage from './components/ChatMessage.svelte'
  import ChatInput from './components/ChatInput.svelte'
  import SkillsBar from './components/SkillsBar.svelte'
  import TypingIndicator from './components/TypingIndicator.svelte'
  import Toast from './components/Toast.svelte'

  let messagesEl: HTMLDivElement
  let skillsList: typeof $skills = $state([])
  let loading = $state(false)
  let messageList: Message[] = $state([])
  let toast: { message: string; kind: 'info' | 'success' | 'error' } | null = $state(null)

  messages.subscribe(v => messageList = v)
  skills.subscribe(v => skillsList = v)
  isLoading.subscribe(v => loading = v)

  function showToast(message: string, kind: 'info' | 'success' | 'error' = 'info') {
    toast = { message, kind }
    setTimeout(() => {
      toast = null
    }, 5000)
  }

  onMount(async () => {
    const loaded = await fetchSkills()
    skills.set(loaded)
  })

  async function scrollToBottom() {
    await tick()
    if (messagesEl) {
      messagesEl.scrollTop = messagesEl.scrollHeight
    }
  }

  async function handleSend(text: string) {
    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: 'user',
      content: text,
      timestamp: Date.now(),
    }

    messages.update(m => [...m, userMsg])
    isLoading.set(true)
    activeSkills.set([])
    await scrollToBottom()

    let convId = ''
    conversationId.subscribe(v => convId = v)()

    const resp = await sendMessage(text, convId)
    isLoading.set(false)

    const botMsg: Message = {
      id: crypto.randomUUID(),
      role: 'assistant',
      content: resp.error ? `[Error] ${resp.error}` : resp.text,
      timestamp: Date.now(),
      skills: resp.skills,
      executionIds: resp.execution_ids,
    }

    messages.update(m => [...m, botMsg])
    if (resp.skills?.length) {
      activeSkills.set(resp.skills)
    }

    // Skill emergence notification — and refresh the skills bar
    if (resp.emerged_skill) {
      showToast(`✨ New skill emerged: ${resp.emerged_skill}`, 'success')
      const updated = await fetchSkills()
      skills.set(updated)
    }

    // Fact extraction notification (subtle)
    if (resp.facts_extracted && resp.facts_extracted > 0) {
      showToast(
        `Learned ${resp.facts_extracted} new fact${resp.facts_extracted > 1 ? 's' : ''}`,
        'info',
      )
    }

    await scrollToBottom()
  }

  function handleRegenerate(originalUserMsg: Message) {
    handleSend(originalUserMsg.content)
  }

  function getPriorUserMessage(idx: number): Message | undefined {
    for (let i = idx - 1; i >= 0; i--) {
      if (messageList[i].role === 'user') return messageList[i]
    }
    return undefined
  }
</script>

{#if toast}
  <Toast message={toast.message} kind={toast.kind} onClose={() => (toast = null)} />
{/if}

<header>
  <h1><span class="dot">●</span> Aiflay</h1>
  <div class="header-meta">
    <div class="status-dot" class:thinking={loading}></div>
    <span>{loading ? 'Thinking...' : 'Ready'}</span>
  </div>
</header>

<SkillsBar skills={skillsList} />

<div class="messages" bind:this={messagesEl}>
  {#each messageList as msg, i (msg.id)}
    {@const prior = msg.role === 'assistant' ? getPriorUserMessage(i) : undefined}
    <ChatMessage
      message={msg}
      onRegenerate={prior ? () => handleRegenerate(prior) : undefined}
    />
  {/each}
  {#if loading}
    <TypingIndicator />
  {/if}
</div>

<ChatInput onSend={handleSend} disabled={loading} />

<style>
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
    flex-shrink: 0;
  }

  header h1 {
    font-size: 18px;
    font-weight: 600;
    letter-spacing: -0.02em;
  }

  .dot { color: var(--accent2); }

  .header-meta {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 12px;
    color: var(--text2);
  }

  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #22c55e;
    transition: background 0.3s;
  }

  .status-dot.thinking {
    background: var(--accent);
    animation: pulse 1.2s ease-in-out infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
  }

  .messages {
    flex: 1;
    overflow-y: auto;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
</style>
