import { create } from 'zustand'
import type { Message, AgentEvent } from '../types'
import type { ApiClient } from '../api'
import { listMessages, streamMessages } from '../api'

export interface MessageStore {
  messages: Record<string, Message[]>
  streamingEvents: AgentEvent[]
  isStreaming: boolean
  error: string | null
  fetchHistory(client: ApiClient, conversationId: string): Promise<void>
  sendMessage(
    client: ApiClient,
    conversationId: string,
    content: string
  ): Promise<void>
  clearStreaming(): void
}

export const useMessageStore = create<MessageStore>((set) => ({
  messages: {},
  streamingEvents: [],
  isStreaming: false,
  error: null,

  async fetchHistory(client: ApiClient, conversationId: string) {
    try {
      const messages = await listMessages(client, conversationId)
      set((s) => ({
        messages: { ...s.messages, [conversationId]: messages },
      }))
    } catch (err) {
      set({ error: (err as Error).message })
    }
  },

  async sendMessage(client: ApiClient, conversationId: string, content: string) {
    set({ isStreaming: true, streamingEvents: [], error: null })
    try {
      for await (const event of streamMessages(
        client.baseUrl,
        conversationId,
        content
      )) {
        set((s) => ({ streamingEvents: [...s.streamingEvents, event] }))
        if (event.type === 'done') break
      }
    } catch (err) {
      set({ error: (err as Error).message })
    } finally {
      set({ isStreaming: false })
    }
  },

  clearStreaming() {
    set({ streamingEvents: [], isStreaming: false })
  },
}))
