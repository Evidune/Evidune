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
    streamMessage,
  } from './lib/api'
  import { generateClientId, generateConversationId } from './lib/ids'
  import type {
    ChatResponse,
    ConversationMode,
    ConversationPlan,
    ConversationSummary,
    Message,
    TaskEvent,
  } from './lib/types'
  import ChatMessage from './components/ChatMessage.svelte'
  import ChatInput from './components/ChatInput.svelte'
  import ConversationList from './components/ConversationList.svelte'
  import ModeToggle from './components/ModeToggle.svelte'
  import PlanPanel from './components/PlanPanel.svelte'
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
  let currentMode: ConversationMode = $state('execute')
  let currentPlan: ConversationPlan | null = $state(null)

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
    return generateConversationId()
  }

  function resetWelcome() {
    messages.set([
      {
        id: 'welcome',
        role: 'assistant',
        content: "Hi! I'm Evidune. Pick a past chat from the sidebar, or start a new one.",
        timestamp: Date.now(),
      },
    ])
  }

  async function startNewConversation() {
    conversationId.set(freshConversationId())
    resetWelcome()
    activeSkills.set([])
    currentMode = 'execute'
    currentPlan = null
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
    currentMode = history.conversation.mode ?? 'execute'
    currentPlan = history.conversation.plan ?? null
    await scrollToBottom()
  }

  function handleModeChange(mode: ConversationMode) {
    currentMode = mode
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
      id: generateClientId(),
      role: 'user',
      content: text,
      timestamp: Date.now(),
    }

    messages.update(m => [...m, userMsg])
    isLoading.set(true)
    activeSkills.set([])
    await scrollToBottom()
    const botId = generateClientId()
    messages.update(m => [
      ...m,
      {
        id: botId,
        role: 'assistant',
        content: '',
        timestamp: Date.now(),
        taskEvents: [],
      },
    ])

    const applyResponse = async (resp: ChatResponse) => {
      messages.update(m =>
        m.map(msg =>
          msg.id === botId
            ? {
                ...msg,
                content: resp.error ? `[Error] ${resp.error}` : resp.text,
                skills: resp.skills,
                executionIds: resp.execution_ids,
                toolTrace: resp.tool_trace,
                taskId: resp.task_id,
                squad: resp.squad,
                taskStatus: resp.task_status,
                taskEvents: resp.task_events ?? msg.taskEvents,
                convergenceSummary: resp.convergence_summary ?? null,
                budgetSummary: resp.budget_summary ?? null,
                environmentId: resp.environment_id ?? null,
                environmentStatus: resp.environment_status ?? null,
                validationSummary: resp.validation_summary ?? null,
                deliverySummary: resp.delivery_summary ?? null,
                artifactManifest: resp.artifact_manifest ?? null,
                skillCreation: resp.skill_creation ?? null,
                skillEvaluations: resp.skill_evaluations ?? [],
              }
            : msg,
        ),
      )
      isLoading.set(false)

      if (resp.skills?.length) {
        activeSkills.set(resp.skills)
      }

      if (resp.mode) {
        currentMode = resp.mode
      }
      currentPlan = resp.plan ?? null

      if (resp.skill_creation) {
        const creation = resp.skill_creation
        const name = creation.skill_name || 'skill'
        if (creation.status === 'created') {
          showToast(`Skill created: ${name}`, 'success')
        } else if (creation.status === 'updated') {
          showToast(`Skill updated: ${name}`, 'success')
        } else if (creation.status === 'reused') {
          showToast(`Using existing skill: ${name}`, 'info')
        } else if (creation.status === 'queued') {
          showToast('Skill creation queued', 'info')
        } else {
          showToast(`Skill creation failed: ${creation.reason ?? 'unknown'}`, 'error')
        }
        if (creation.status === 'created' || creation.status === 'updated') {
          const updated = await fetchSkills()
          skills.set(updated)
        }
      } else if (resp.emerged_skill) {
        showToast(`New skill emerged: ${resp.emerged_skill}`, 'success')
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

      await refreshConversations()
      await scrollToBottom()
    }

    const updateTask = (event: TaskEvent) => {
      messages.update(m =>
        m.map(msg =>
          msg.id === botId
            ? {
                ...msg,
                taskEvents: [...(msg.taskEvents ?? []), event],
              }
            : msg,
        ),
      )
      scrollToBottom()
    }

    let finished = false
    streamMessage(
      text,
      activeConvId,
      {
        onTask: event => updateTask(event),
        onDone: resp => {
          finished = true
          applyResponse(resp)
        },
        onError: async () => {
          if (finished) return
          const resp = await sendMessage(text, activeConvId, undefined, currentMode)
          await applyResponse(resp)
        },
      },
      undefined,
      currentMode,
    )
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
      <h1><span class="dot">●</span> Evidune</h1>
      <div class="header-meta">
        <ModeToggle mode={currentMode} onChange={handleModeChange} disabled={loading} />
        <div class="status-dot" class:thinking={loading}></div>
        <span>{loading ? 'Thinking...' : 'Ready'}</span>
      </div>
    </header>

    <SkillsBar skills={skillsList} />

    {#if currentMode === 'plan' || currentPlan}
      <PlanPanel mode={currentMode} plan={currentPlan} />
    {/if}

    <div class="messages" bind:this={messagesEl} data-testid="message-list">
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
