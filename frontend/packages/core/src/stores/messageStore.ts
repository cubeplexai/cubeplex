// frontend/packages/core/src/stores/messageStore.ts
import { create } from 'zustand'
import type {
  ContentBlock, TodoItem,
  Message, TextDeltaEvent, ToolCallEvent, ToolCallDeltaEvent,
  ToolResultEvent, ReasoningEvent, ArtifactEventData,
} from '../types'
import type { ApiClient } from '../api'
import { listMessages, streamMessages } from '../api'
import { useCitationStore } from './citationStore'

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
  todos: TodoItem[]
  toolStartedMap: Record<string, number>
  toolResultMap: Record<
    string,
    { content: string; receivedAt: number; startedAt?: number; contentType?: string }
  >

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
  const exactMatchIndex = finalized.findIndex(
    (block) => block.type === 'tool_call_streaming' && block.tool_call_id === toolCallId,
  )
  let fallbackMatchIndex = -1
  for (let i = finalized.length - 1; i >= 0; i--) {
    const block = finalized[i]
    if (block.type === 'tool_call_streaming' && block.tool_call_id === null && block.name === name) {
      fallbackMatchIndex = i
      break
    }
  }
  const matchIndex = exactMatchIndex >= 0 ? exactMatchIndex : fallbackMatchIndex
  const nextBlocks = matchIndex >= 0
    ? finalized.filter((_, index) => index !== matchIndex)
    : finalized

  return [...nextBlocks, { type: 'tool_call', name, arguments: args, tool_call_id: toolCallId }]
}

function normalizeTodoStatus(status: unknown): TodoItem['status'] {
  return status === 'in_progress' || status === 'completed' ? status : 'pending'
}

function parseTodosFromToolCall(
  args: Record<string, unknown>,
): TodoItem[] {
  const rawTodos = Array.isArray(args.todos) ? args.todos : []
  const todos: TodoItem[] = []

  for (const todo of rawTodos) {
    if (!todo || typeof todo !== 'object') continue
    const raw = todo as { content?: unknown; status?: unknown }
    const description = typeof raw.content === 'string' ? raw.content.trim() : ''
    if (!description) continue
    todos.push({
      id: null,
      description,
      status: normalizeTodoStatus(raw.status),
    })
  }

  return todos
}

/**
 * Batched state updater: collects multiple set() calls within a single microtask
 * and flushes them as one Zustand update. This prevents N SSE events arriving in
 * one chunk from causing N separate re-renders.
 */
function createBatcher(set: (updater: (s: MessageStore) => Partial<MessageStore>) => void) {
  let pending: Array<(s: MessageStore) => Partial<MessageStore>> = []
  let scheduled = false

  const flush = () => {
    if (pending.length === 0) return
    const batch = pending
    pending = []
    scheduled = false
    set((state) => {
      let merged = state
      for (const fn of batch) {
        merged = { ...merged, ...fn(merged) }
      }
      return merged
    })
  }

  const batchedSet = (updater: (s: MessageStore) => Partial<MessageStore>) => {
    pending.push(updater)
    if (!scheduled) {
      scheduled = true
      queueMicrotask(flush)
    }
  }

  return { batchedSet, flush }
}

export const useMessageStore = create<MessageStore>((set, get) => ({
  messages: {},
  streamAgents: {},
  isStreaming: false,
  statusPhase: null,
  error: null,
  todos: [],
  toolStartedMap: {},
  toolResultMap: {},

  async loadMessages(client: ApiClient, conversationId: string) {
    if (get().isStreaming) return
    try {
      const messages = await listMessages(client, conversationId)
      // Re-check after await: if streaming started while we were fetching,
      // discard the API response to preserve the optimistic user message.
      if (get().isStreaming) return

      // Restore todos from the last write_todos tool call in history
      let restoredTodos: TodoItem[] = []
      for (let i = messages.length - 1; i >= 0; i--) {
        const msg = messages[i]
        if (msg.role !== 'assistant' || !msg.tool_calls) continue
        const tc = msg.tool_calls.find((t) => t.name === 'write_todos')
        if (tc) {
          restoredTodos = parseTodosFromToolCall(tc.arguments)
          break
        }
      }

      // Restore citations from tool messages in history
      for (const msg of messages) {
        if (msg.role === 'tool' && msg.citations?.length) {
          useCitationStore.getState().loadCitations(conversationId, msg.citations)
        }
      }

      set((s) => ({
        messages: { ...s.messages, [conversationId]: messages },
        todos: restoredTodos,
        error: null,
        // Clear completed stream state — history messages are now source of truth
        streamAgents: {},
        toolResultMap: {},
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
      todos: [],
      toolStartedMap: {},
      toolResultMap: {},
    }))

    const { batchedSet, flush } = createBatcher(set)

    try {
      for await (const event of streamMessages(client.baseUrl, conversationId, content)) {
        const agentKey = event.agent_id ?? MAIN_AGENT_KEY

        if (event.type === 'text_delta') {
          const e = event as TextDeltaEvent
          batchedSet((s) => {
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
          batchedSet((s) => {
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
          batchedSet((s) => {
            const prev =
              s.streamAgents[agentKey]
              ?? emptyStream(event.agent_name)
            const existingStartedAt = s.toolStartedMap[e.data.tool_call_id]

            let nextTodos = s.todos
            if (e.data.name === 'write_todos') {
              nextTodos = parseTodosFromToolCall(e.data.arguments)
            }

            return {
              todos: nextTodos,
              toolStartedMap: {
                ...s.toolStartedMap,
                [e.data.tool_call_id]: existingStartedAt
                  ?? (e.data.started_at ? new Date(e.data.started_at).getTime() : Date.now()),
              },
              streamAgents: {
                ...s.streamAgents,
                [agentKey]: {
                  ...prev,
                  toolCalls: [...prev.toolCalls, e],
                  blocks: appendToolCallBlock(
                    prev.blocks,
                    e.data.name,
                    e.data.arguments,
                    e.data.tool_call_id,
                  ),
                },
              },
            }
          })
        } else if (event.type === 'tool_call_delta') {
          const e = event as ToolCallDeltaEvent
          batchedSet((s) => {
            const prev = s.streamAgents[agentKey] ?? emptyStream(event.agent_name)
            const idx = e.data.index ?? 0
            const blocks = [...prev.blocks]
            const startedAt = e.timestamp ? new Date(e.timestamp).getTime() : Date.now()
            const nextToolStartedMap = e.data.tool_call_id && !s.toolStartedMap[e.data.tool_call_id]
              ? {
                  ...s.toolStartedMap,
                  [e.data.tool_call_id]: startedAt,
                }
              : s.toolStartedMap

            // Find existing streaming block for this index
            const existingIdx = blocks.findIndex(
              (b) => b.type === 'tool_call_streaming' && b.index === idx,
            )

            if (existingIdx >= 0) {
              const existing = blocks[existingIdx] as Extract<
                ContentBlock, { type: 'tool_call_streaming' }
              >
              blocks[existingIdx] = {
                ...existing,
                args_text: existing.args_text + (e.data.args_delta || ''),
                tool_call_id: e.data.tool_call_id ?? existing.tool_call_id,
              }
              return {
                toolStartedMap: nextToolStartedMap,
                streamAgents: {
                  ...s.streamAgents,
                  [agentKey]: { ...prev, blocks },
                },
              }
            }

            // Create new streaming block
            const finalized = finalizeLastReasoning(blocks)
            finalized.push({
              type: 'tool_call_streaming',
              name: e.data.name ?? '',
              args_text: e.data.args_delta || '',
              tool_call_id: e.data.tool_call_id ?? null,
              index: idx,
            })
            return {
              toolStartedMap: nextToolStartedMap,
              streamAgents: {
                ...s.streamAgents,
                [agentKey]: { ...prev, blocks: finalized },
              },
            }
          })
        } else if (event.type === 'tool_result') {
          const e = event as ToolResultEvent
          const tcId = e.data.tool_call_id ?? ''
          batchedSet((s) => {
            const newMap = { ...s.toolResultMap }
            if (tcId) {
              newMap[tcId] = {
                content: e.data.content,
                receivedAt: Date.now(),
                startedAt: s.toolStartedMap[tcId]
                  ?? (e.data.started_at ? new Date(e.data.started_at).getTime() : undefined),
                contentType: e.data.content_type,
              }
            }

            return {
              toolResultMap: newMap,
              streamAgents: {
                ...s.streamAgents,
                [agentKey]: {
                  ...(s.streamAgents[agentKey]
                    ?? emptyStream(event.agent_name)),
                  toolResults: [
                    ...(s.streamAgents[agentKey]
                      ?.toolResults ?? []),
                    e,
                  ],
                },
              },
            }
          })
        } else if (event.type === 'artifact') {
          const artifactData = event.data as unknown as ArtifactEventData
          if (artifactData.artifact) {
            const { useArtifactStore } = await import('./artifactStore')
            useArtifactStore.getState().addOrUpdate(
              conversationId,
              artifactData.artifact,
            )
          }
        } else if (event.type === 'citation') {
          const citationData = event.data as unknown as import('../types').CitationData
          useCitationStore.getState().addCitation(conversationId, citationData)
        } else if (event.type === 'status') {
          batchedSet(() => ({ statusPhase: (event.data as { phase: string }).phase }))
        } else if (event.type === 'done') {
          break
        } else if (event.type === 'error') {
          const errData = event.data as { message: string; details?: string }
          set({ error: errData.details || errData.message })
          break
        }
      }
    } catch (err) {
      set({ error: (err as Error).message })
    } finally {
      // Flush any pending batched updates so streamAgents is fully up-to-date
      flush()

      // Build final assistant message from accumulated main agent stream
      const agents = get().streamAgents
      const mainStream = agents[MAIN_AGENT_KEY]
      if (mainStream) {
        // Finalize any pending reasoning block and strip internal started_at field
        const finalBlocks = finalizeLastReasoning(mainStream.blocks)
          .filter((b) => b.type !== 'tool_call_streaming')
          .map((b) => {
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
                tool_call_id: tc.data.tool_call_id,
                started_at: tc.data.started_at ?? null,
              }))
            : null,
          reasoning: mainStream.reasoning || null,
          blocks: finalBlocks.length > 0 ? finalBlocks : null,
          created_at: new Date().toISOString(),
        }

        // Build tool messages for subagent streams so cards render after streaming ends
        const toolMessages: Message[] = []
        // Extract role/task from main stream's subagent tool_call blocks
        const subagentArgs: Record<string, { role?: string; task?: string }> = {}
        for (const block of finalBlocks) {
          if (block.type === 'tool_call' && block.name === 'subagent') {
            const args = block.arguments as { role?: string; task?: string }
            subagentArgs[`subagent:${block.tool_call_id}`] = args
          }
        }
        for (const [key, agentStream] of Object.entries(agents)) {
          if (key === MAIN_AGENT_KEY) continue
          // key is like "subagent:<tool_call_id>"
          const toolCallId = key.startsWith('subagent:') ? key.slice(9) : key
          const args = subagentArgs[key]
          toolMessages.push({
            id: `tool-${toolCallId}-${Date.now()}`,
            role: 'tool',
            content: agentStream.text || null,
            name: 'subagent',
            tool_call_id: toolCallId,
            subagent_events: {
              text: agentStream.text,
              tool_calls: agentStream.toolCalls.map((tc) => ({
                name: tc.data.name,
                arguments: tc.data.arguments,
              })),
              reasoning: agentStream.reasoning,
              role: args?.role,
              task: args?.task,
            },
            created_at: new Date().toISOString(),
          })
        }

        set((s) => ({
          messages: {
            ...s.messages,
            [conversationId]: [
              ...(s.messages[conversationId] ?? []),
              assistantMessage,
              ...toolMessages,
            ],
          },
          isStreaming: false,
          statusPhase: null,
          // Keep streamAgents intact — the same AssistantMessage component stays
          // mounted and transitions smoothly from streaming to completed state.
          // Cleared on next send() or loadMessages().
        }))
      } else {
        set({ isStreaming: false, statusPhase: null })
      }
    }
  },

  clearStream() {
    set({
      streamAgents: {},
      isStreaming: false,
      statusPhase: null,
      todos: [],
      toolStartedMap: {},
      toolResultMap: {},
    })
  },
}))
