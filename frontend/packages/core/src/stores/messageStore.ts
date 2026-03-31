// frontend/packages/core/src/stores/messageStore.ts
import { create } from 'zustand'
import type {
  ContentBlock,
  Message, TextDeltaEvent, ToolCallEvent, ToolResultEvent, ReasoningEvent,
} from '../types'
import type { ApiClient } from '../api'
import { listMessages, streamMessages } from '../api'

export interface AgentStream {
  text: string
  toolCalls: ToolCallEvent[]
  toolResults: ToolResultEvent[]
  reasoning: string
  blocks: ContentBlock[]
  name: string | null
}

export interface MessageStore {
  messages: Record<string, Message[]>
  streamAgents: Record<string, AgentStream>   // "main" or "task:xxx"
  isStreaming: boolean
  statusPhase: string | null
  error: string | null

  loadMessages(client: ApiClient, conversationId: string): Promise<void>
  send(client: ApiClient, conversationId: string, content: string): Promise<void>
  clearStream(): void
}

const MAIN_AGENT_KEY = 'main'

function emptyStream(name: string | null = null): AgentStream {
  return { text: '', toolCalls: [], toolResults: [], reasoning: '', blocks: [], name }
}

/** Finalize the last reasoning block's duration if switching to a different block type */
function finalizeLastReasoning(blocks: ContentBlock[]): ContentBlock[] {
  const last = blocks[blocks.length - 1]
  if (last?.type === 'reasoning' && last.started_at && !last.duration_ms) {
    const updated = [...blocks]
    updated[updated.length - 1] = { ...last, duration_ms: Date.now() - last.started_at }
    return updated
  }
  return blocks
}

/** Append content to blocks, merging with the last block if same type, or creating new block */
function appendBlock(
  blocks: ContentBlock[], type: 'reasoning' | 'text', content: string,
): ContentBlock[] {
  const last = blocks[blocks.length - 1]
  if (last && last.type === type) {
    const updated = [...blocks]
    updated[updated.length - 1] = { ...last, content: last.content + content }
    return updated
  }
  // Switching type — finalize any pending reasoning block
  const finalized = finalizeLastReasoning(blocks)
  if (type === 'reasoning') {
    return [...finalized, { type, content, started_at: Date.now() }]
  }
  return [...finalized, { type, content }]
}

function appendToolCallBlock(
  blocks: ContentBlock[],
  name: string,
  args: Record<string, unknown>,
  toolCallId: string,
): ContentBlock[] {
  const finalized = finalizeLastReasoning(blocks)
  return [...finalized, { type: 'tool_call', name, arguments: args, tool_call_id: toolCallId }]
}

export const useMessageStore = create<MessageStore>((set, get) => ({
  messages: {},
  streamAgents: {},
  isStreaming: false,
  statusPhase: null,
  error: null,

  async loadMessages(client: ApiClient, conversationId: string) {
    if (get().isStreaming) return
    try {
      const messages = await listMessages(client, conversationId)
      // Re-check after await: if streaming started while we were fetching,
      // discard the API response to preserve the optimistic user message.
      if (get().isStreaming) return
      set((s) => ({
        messages: { ...s.messages, [conversationId]: messages },
        error: null,
      }))
    } catch (err) {
      set({ error: (err as Error).message })
    }
  },

  async send(client: ApiClient, conversationId: string, content: string) {
    // Optimistic: add user message immediately to the correct conversation
    const userMessage: Message = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    }

    set((s) => ({
      messages: {
        ...s.messages,
        [conversationId]: [...(s.messages[conversationId] ?? []), userMessage],
      },
      streamAgents: { [MAIN_AGENT_KEY]: emptyStream() },
      isStreaming: true,
      statusPhase: null,
      error: null,
    }))

    try {
      for await (const event of streamMessages(client.baseUrl, conversationId, content)) {
        const agentKey = event.agent_id ?? MAIN_AGENT_KEY

        if (event.type === 'text_delta') {
          const e = event as TextDeltaEvent
          set((s) => {
            const prev = s.streamAgents[agentKey] ?? emptyStream(event.agent_name)
            return {
              streamAgents: {
                ...s.streamAgents,
                [agentKey]: {
                  ...prev,
                  text: prev.text + e.data.content,
                  blocks: appendBlock(prev.blocks, 'text', e.data.content),
                },
              },
            }
          })
        } else if (event.type === 'reasoning') {
          const e = event as ReasoningEvent
          set((s) => {
            const prev = s.streamAgents[agentKey] ?? emptyStream(event.agent_name)
            return {
              streamAgents: {
                ...s.streamAgents,
                [agentKey]: {
                  ...prev,
                  reasoning: prev.reasoning + e.data.content,
                  blocks: appendBlock(prev.blocks, 'reasoning', e.data.content),
                },
              },
            }
          })
        } else if (event.type === 'tool_call') {
          const e = event as ToolCallEvent
          set((s) => {
            const prev = s.streamAgents[agentKey] ?? emptyStream(event.agent_name)
            return {
              streamAgents: {
                ...s.streamAgents,
                [agentKey]: {
                  ...prev,
                  toolCalls: [...prev.toolCalls, e],
                  blocks: appendToolCallBlock(
                    prev.blocks, e.data.name, e.data.arguments, e.data.tool_call_id,
                  ),
                },
              },
            }
          })
        } else if (event.type === 'tool_result') {
          const e = event as ToolResultEvent
          set((s) => ({
            streamAgents: {
              ...s.streamAgents,
              [agentKey]: {
                ...s.streamAgents[agentKey] ?? emptyStream(event.agent_name),
                toolResults: [...(s.streamAgents[agentKey]?.toolResults ?? []), e],
              },
            },
          }))
        } else if (event.type === 'status') {
          set({ statusPhase: (event.data as { phase: string }).phase })
        } else if (event.type === 'done') {
          break
        } else if (event.type === 'error') {
          set({ error: (event.data as { message: string }).message })
          break
        }
      }
    } catch (err) {
      set({ error: (err as Error).message })
    } finally {
      // Build final assistant message from accumulated main agent stream
      const mainStream = get().streamAgents[MAIN_AGENT_KEY]
      if (mainStream) {
        // Finalize any pending reasoning block and strip internal started_at field
        const finalBlocks = finalizeLastReasoning(mainStream.blocks).map((b) => {
          if (b.type === 'reasoning') {
            const { started_at: _, ...rest } = b
            return rest
          }
          return b
        })
        const assistantMessage: Message = {
          id: `assistant-${Date.now()}`,
          role: 'assistant',
          content: mainStream.text || null,
          tool_calls: mainStream.toolCalls.length > 0
            ? mainStream.toolCalls.map((tc) => ({
                name: tc.data.name,
                arguments: tc.data.arguments,
              }))
            : null,
          reasoning: mainStream.reasoning || null,
          blocks: finalBlocks.length > 0 ? finalBlocks : null,
          created_at: new Date().toISOString(),
        }
        set((s) => ({
          messages: {
            ...s.messages,
            [conversationId]: [
              ...(s.messages[conversationId] ?? []),
              assistantMessage,
            ],
          },
          isStreaming: false,
          statusPhase: null,
          streamAgents: {},
        }))
      } else {
        set({ isStreaming: false, statusPhase: null, streamAgents: {} })
      }
    }
  },

  clearStream() {
    set({ streamAgents: {}, isStreaming: false, statusPhase: null })
  },
}))
