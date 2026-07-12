import { describe, it, expect } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useMessageScopedToolResults } from '@/hooks/useMessageScopedToolResults'
import type { Message } from '@cubeplex/core'

type Entry = { content: string; receivedAt: number }

function assistantMessage(id: string, toolCallIds: string[]): Message {
  return {
    id,
    role: 'assistant',
    content: [
      { type: 'text', text: `m-${id}` },
      ...toolCallIds.map((tcId) => ({
        type: 'tool_call' as const,
        id: tcId,
        name: 'fake',
        arguments: {},
      })),
    ],
    timestamp: 0,
  } as unknown as Message
}

describe('useMessageScopedToolResults', () => {
  it('returns a stable reference for a message when no relevant entry changes', () => {
    const m1 = assistantMessage('msg-1', ['tc-a'])
    const messages = [m1]
    const historical = { 'tc-a': { content: 'hist', receivedAt: 1 } } as Record<string, Entry>

    const { result, rerender } = renderHook(
      ({ live }) => useMessageScopedToolResults(messages, historical, live),
      { initialProps: { live: {} as Record<string, Entry> } },
    )
    const first = result.current['msg-1']
    expect(first).toEqual({ 'tc-a': { content: 'hist', receivedAt: 1 } })

    // New `live` reference but no entry for tc-a: subset is unchanged → same ref.
    rerender({ live: { 'tc-other': { content: 'x', receivedAt: 9 } } })
    expect(result.current['msg-1']).toBe(first)
  })

  it('returns a new reference for a message when a relevant live entry arrives', () => {
    const m1 = assistantMessage('msg-1', ['tc-a'])
    const messages = [m1]
    const historical = {} as Record<string, Entry>

    const { result, rerender } = renderHook(
      ({ live }) => useMessageScopedToolResults(messages, historical, live),
      { initialProps: { live: {} as Record<string, Entry> } },
    )
    const first = result.current['msg-1']
    expect(first).toEqual({})

    rerender({ live: { 'tc-a': { content: 'live!', receivedAt: 7 } } })
    expect(result.current['msg-1']).not.toBe(first)
    expect(result.current['msg-1']['tc-a']).toEqual({ content: 'live!', receivedAt: 7 })
  })

  it('live entry wins over historical for the same id', () => {
    const m1 = assistantMessage('msg-1', ['tc-a'])
    const messages = [m1]
    const historical = { 'tc-a': { content: 'hist', receivedAt: 1 } } as Record<string, Entry>
    const live = { 'tc-a': { content: 'live', receivedAt: 2 } } as Record<string, Entry>

    const { result } = renderHook(() => useMessageScopedToolResults(messages, historical, live))
    expect(result.current['msg-1']['tc-a']).toEqual({ content: 'live', receivedAt: 2 })
  })

  it('only the affected message gets a new subset reference', () => {
    const m1 = assistantMessage('msg-1', ['tc-a'])
    const m2 = assistantMessage('msg-2', ['tc-b'])
    const messages = [m1, m2]
    const historical = {} as Record<string, Entry>

    const { result, rerender } = renderHook(
      ({ live }) => useMessageScopedToolResults(messages, historical, live),
      { initialProps: { live: {} as Record<string, Entry> } },
    )
    const before1 = result.current['msg-1']
    const before2 = result.current['msg-2']

    rerender({ live: { 'tc-a': { content: 'a-live', receivedAt: 5 } } })
    expect(result.current['msg-1']).not.toBe(before1)
    // msg-2 has no relevant live entry → ref unchanged.
    expect(result.current['msg-2']).toBe(before2)
  })

  it('returns the same empty-object reference for messages with no tool calls', () => {
    const m1 = assistantMessage('msg-1', [])
    const m2 = assistantMessage('msg-2', [])
    const { result } = renderHook(() => useMessageScopedToolResults([m1, m2], {}, {}))
    // Identity-equal — both messages share the singleton EMPTY subset.
    expect(result.current['msg-1']).toBe(result.current['msg-2'])
    expect(Object.keys(result.current['msg-1'])).toHaveLength(0)
  })

  it('includes inner subagent tool_call_ids stored on the matching tool_result message', () => {
    // Assistant message with one `subagent` tool_call (outer id sa-1).
    const assistant: Message = {
      id: 'msg-1',
      role: 'assistant',
      content: [
        {
          type: 'tool_call' as const,
          id: 'sa-1',
          name: 'subagent',
          arguments: { role: 'helper', task: 'do stuff' },
        },
      ],
      timestamp: 0,
    } as unknown as Message

    // The subagent's tool_result message carries inner tool_call_ids in its
    // `subagent_events` metadata (this is how buildHistoricalToolResultMap
    // flattens them into the global historical map).
    const subagentResult: Message = {
      id: 'tr-1',
      role: 'tool_result',
      tool_call_id: 'sa-1',
      tool_name: 'subagent',
      content: [{ type: 'text', text: 'done' }],
      timestamp: 0,
      metadata: {
        subagent_events: {
          text: 'inner work',
          tool_calls: [{ name: 'web_fetch', arguments: {}, id: 'inner-x' }],
          tool_results: [
            {
              tool_name: 'web_fetch',
              tool_call_id: 'inner-x',
              content: 'inner result',
            },
          ],
          thinking: '',
        },
      },
    } as unknown as Message

    const historical = {
      'sa-1': { content: 'outer subagent', receivedAt: 1 },
      'inner-x': { content: 'inner result', receivedAt: 2 },
    } as Record<string, Entry>

    const { result } = renderHook(() =>
      useMessageScopedToolResults([assistant, subagentResult], historical, {}),
    )
    const subset = result.current['msg-1']
    expect(subset['sa-1']).toEqual({ content: 'outer subagent', receivedAt: 1 })
    expect(subset['inner-x']).toEqual({ content: 'inner result', receivedAt: 2 })
  })
})
