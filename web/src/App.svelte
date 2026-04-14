<script lang="ts">
  import { onMount, tick } from 'svelte'
  import { messages, skills, activeSkills, isLoading, conversationId } from './lib/stores'
  import {
    archiveConversation,
    deleteConversation,
    fetchConversationHistory,
    fetchConversations,
    fetchSkills,
    sendMessage,
  } from './lib/api'
  import type { ConversationSummary, Message } from './lib/types'
  import ChatMessage from './components/ChatMessage.svelte'
  import ChatInput from './components/ChatInput.svelte'
  import ConversationList from './components/ConversationList.svelte'
  import SkillsBar from './components/SkillsBar.svelte'
  import TypingIndicator from './components/TypingIndicator.svelte'
  import Toast from './components/Toast.svelte'

  let messagesEl: HTMLDivElement
  let skillsList: typeof $skills = $state([])
  let loading = $state(false)
  let messageList: Message[] = $state([])
  let toast: { message: string; kind: 'info' | 'success' | 'error' } | null = $state(null)
  let conversations: ConversationSummary[] = $state([])
  let activeConvId = $state('')

  messages.subscribe(v => (messageList = v))
  skills.subscribe(v => (skillsList = v))
  isLoading.subscribe(v => (loading = v))
  conversationId.subscribe(v => (activeConvId = v))

  function showToast(message: string, kind: 'info' | 'success' | 'error' = 'info') {
    toast = { message, kind }
    setTimeout(() => {
      toast = null
    }, 5000)
  }

  async function refreshConversations() {
    conversations = await fetchConversations()
  }

  function freshConversationId(): string {
    return `web-${crypto.randomUUID().slice(0, 8)}`
  }

  function resetWelcome() {
    messages.set([
      {
        id: 'welcome',
        role: 'assistant',
        content: "Hi! I'm Aiflay. Pick a past chat from the sidebar, or start a new one.",
        timestamp: Date.now(),
      },
    ])
  }

  async function startNewConversation() {
    conversationId.set(freshConversationId())
    resetWelcome()
    activeSkills.set([])
    await scrollToBottom()
  }

  async function selectConversation(id: string) {
    if (id === activeConvId) return
    conversationId.set(id)
    const history = await fetchConversationHistory(id)
    if (!history) return
    const mapped: Message[] = history.messages.map((m, idx) => ({
      id: `${id}-${idx}`,
      role: m.role === 'assistant' ? 'assistant' : 'user',
      content: m.content,
      timestamp: Date.now() + idx,
    }))
    messages.set(mapped)
    activeSkills.set([])
    await scrollToBottom()
  }

  async function handleDelete(id: string) {
    const ok = await deleteConversation(id)
    if (!ok) {
      showToast('Failed to delete', 'error')
      return
    }
    await refreshConversations()
    if (id === activeConvId) {
      await startNewConversation()
    }
  }

  async function handleArchive(id: string) {
    const ok = await archiveConversation(id)
    if (!ok) {
      showToast('Failed to archive', 'error')
      return
    }
    await refreshConversations()
    if (id === activeConvId) {
      await startNewConversation()
    }
  }

  onMount(async () => {
    const [loadedSkills] = await Promise.all([fetchSkills(), refreshConversations()])
    skills.set(loadedSkills)
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

    const resp = await sendMessage(text, activeConvId)
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

    if (resp.emerged_skill) {
      showToast(`✨ New skill emerged: ${resp.emerged_skill}`, 'success')
      const updated = await fetchSkills()
      skills.set(updated)
    }

    if (resp.facts_extracted && resp.facts_extracted > 0) {
      showToast(
        `Learned ${resp.facts_extracted} new fact${resp.facts_extracted > 1 ? 's' : ''}`,
        'info',
      )
    }

    if (resp.new_title) {
      showToast(`Titled: ${resp.new_title}`, 'info')
    }

    // Refresh sidebar so the just-used conversation bubbles up
    await refreshConversations()

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

<div class="app-shell">
  <ConversationList
    conversations={conversations}
    activeId={activeConvId}
    onSelect={selectConversation}
    onNew={startNewConversation}
    onDelete={handleDelete}
    onArchive={handleArchive}
  />

  <main class="main">
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
  </main>
</div>

<style>
  .app-shell {
    display: flex;
    height: 100vh;
    width: 100%;
  }

  .main {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }

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

  .dot {
    color: var(--accent2);
  }

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
    0%,
    100% {
      opacity: 1;
    }
    50% {
      opacity: 0.3;
    }
  }

  .messages {
    flex: 1;
    overflow-y: auto;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  @media (max-width: 640px) {
    .app-shell {
      flex-direction: column;
    }
  }
</style>
