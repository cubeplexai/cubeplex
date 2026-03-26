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
    // 乐观地立即添加用户消息
    const userMessage: Message = {
      id: `temp-user-${Date.now()}`,
      conversation_id: conversationId,
      role: 'user',
      content,
      events: null,
      created_at: new Date().toISOString(),
    }

    set((s) => ({
      messages: {
        ...s.messages,
        [conversationId]: [...(s.messages[conversationId] || []), userMessage],
      },
      streamingEvents: [],
      isStreaming: true,
      error: null,
    }))

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
      // 刷新历史记录，获取正式保存的 assistant 消息
      try {
        const messages = await listMessages(client, conversationId)
        set((s) => ({
          messages: { ...s.messages, [conversationId]: messages },
          isStreaming: false,
          streamingEvents: [],
        }))
      } catch {
        set({ isStreaming: false, streamingEvents: [] })
      }
    }
  },

  clearStreaming() {
    set({ streamingEvents: [], isStreaming: false })
  },
}))
