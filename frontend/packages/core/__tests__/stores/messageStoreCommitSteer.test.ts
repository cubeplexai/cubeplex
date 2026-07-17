import { describe, it, expect, beforeEach } from 'vitest'
import { useMessageStore } from '../../src/stores/messageStore'

function applyInjected(convId: string, content: string, steerId: string) {
  return useMessageStore.getState().__commitTurnAndInject(convId, {
    content,
    steer_id: steerId,
  })
}

describe('commit on injected_message', () => {
  beforeEach(() => {
    useMessageStore.setState({
      messages: {
        c1: [
          {
            id: 'u1',
            role: 'user',
            content: [{ type: 'text', text: 'go' }],
            timestamp: 1,
            metadata: {},
          },
        ],
      },
      streamAgents: {
        main: {
          text: 'partial',
          toolCalls: [],
          toolResults: [],
          thinking: '',
          blocks: [{ type: 'text', text: 'partial' }],
          name: null,
        },
      },
      pendingSteers: { c1: [{ steerId: 's1', text: 'do X' }] },
      toolResultMap: {},
      turnUsage: { c1: null },
      isStreaming: true,
      streamingConversationId: 'c1',
    })
  })

  it('finalizes the current bubble, inserts the steer user msg, resets streams, clears pending', () => {
    applyInjected('c1', 'do X', 's1')
    const s = useMessageStore.getState()
    const roles = s.messages.c1.map((m) => m.role)
    expect(roles).toEqual(['user', 'assistant', 'user'])
    expect(s.messages.c1[2].content[0]).toMatchObject({ type: 'text', text: 'do X' })
    expect(s.messages.c1[2].metadata?.steer_id).toBe('s1')
    expect(s.streamAgents.main.text).toBe('')
    expect(s.pendingSteers.c1 ?? []).toHaveLength(0)
  })

  it('skips the empty assistant bubble when the main stream has no content', () => {
    useMessageStore.setState({
      streamAgents: {
        main: { text: '', toolCalls: [], toolResults: [], thinking: '', blocks: [], name: null },
      },
    })
    applyInjected('c1', 'do X', 's1')
    const roles = useMessageStore.getState().messages.c1.map((m) => m.role)
    expect(roles).toEqual(['user', 'user'])
  })

  it('stamps group-chat sender identity onto the injected user message', () => {
    useMessageStore.getState().__commitTurnAndInject('c1', {
      content: 'do X',
      steer_id: 's1',
      sender_user_id: 'user_abc',
      sender_display_name: 'Alice',
    })
    const injected = useMessageStore.getState().messages.c1.at(-1)
    expect(injected?.metadata?.sender_user_id).toBe('user_abc')
    expect(injected?.metadata?.sender_display_name).toBe('Alice')
  })

  it('omits sender identity for non-group steers', () => {
    applyInjected('c1', 'do X', 's1')
    const injected = useMessageStore.getState().messages.c1.at(-1)
    expect(injected?.metadata?.sender_user_id).toBeUndefined()
    expect(injected?.metadata?.sender_display_name).toBeUndefined()
  })

  it('is idempotent — re-applying the same steer_id is a no-op', () => {
    applyInjected('c1', 'do X', 's1')
    const before = useMessageStore.getState().messages.c1.length
    applyInjected('c1', 'do X', 's1')
    expect(useMessageStore.getState().messages.c1.length).toBe(before)
  })

  it('clearStream clears all pending steers', () => {
    useMessageStore.setState({ pendingSteers: { c1: [{ steerId: 's1', text: 'do X' }] } })
    useMessageStore.getState().clearStream()
    expect(useMessageStore.getState().pendingSteers).toEqual({})
  })
})
