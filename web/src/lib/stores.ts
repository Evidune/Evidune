import { writable } from 'svelte/store'
import type { Message, Skill } from './types'

export const messages = writable<Message[]>([
  {
    id: 'welcome',
    role: 'assistant',
    content: 'Hi! I\'m Aiflay, your AI assistant powered by skills and persistent memory.',
    timestamp: Date.now(),
  },
])

export const skills = writable<Skill[]>([])
export const activeSkills = writable<string[]>([])
export const isLoading = writable(false)
export const conversationId = writable(`web-${crypto.randomUUID().slice(0, 8)}`)
