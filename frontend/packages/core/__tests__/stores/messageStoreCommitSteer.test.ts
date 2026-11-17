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
      currentRunId: 'run-1',
      isStreaming: true,
      streamingConversationId: 'c1',
    })
  })

  it('finalizes the current bubble, inserts the steer user msg, resets streams, clears pending', () => {
    applyInjected('c1', 'do X', 's1')
    const s = useMessageStore.getState()
    const roles = s.messages.c1.map((m) => m.role)
    expect(roles).toEqual(['user', 'assistant', 'user'])
    expect(s.messages.c1[1].run_id).toBe('run-1')
    expect(s.messages.c1[2].run_id).toBe('run-1')
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

  it('clears a stale pending chip when history already contains the steer', () => {
    applyInjected('c1', 'do X', 's1')
    useMessageStore.setState({
      pendingSteers: { c1: [{ steerId: 's1', text: 'do X' }] },
    })

    applyInjected('c1', 'do X', 's1')

    expect(useMessageStore.getState().pendingSteers.c1).toEqual([])
  })

  it('clearStream clears all pending steers', () => {
    useMessageStore.setState({ pendingSteers: { c1: [{ steerId: 's1', text: 'do X' }] } })
    useMessageStore.getState().clearStream()
    expect(useMessageStore.getState().pendingSteers).toEqual({})
  })

  it('preserves subagent tool_result when child subagent stream bucket is absent', () => {
    const apply = useMessageStore.getState().__applyEvent
    useMessageStore.setState({
      streamAgents: {},
      toolStartedMap: {},
      toolResultMap: {},
      currentRunId: 'run-1',
      isStreaming: true,
      streamingConversationId: 'c1',
    })
    apply({
      type: 'tool_call',
      timestamp: '2026-11-17T12:00:00.000Z',
      data: {
        tool_call_id: 'call_sub_1',
        name: 'subagent',
        arguments: { role: 'Researcher', task: 'check logs' },
      },
      agent_id: null,
      agent_name: null,
    })
    apply({
      type: 'tool_result',
      timestamp: '2026-11-17T12:00:01.000Z',
      data: {
        tool_call_id: 'call_sub_1',
        name: 'subagent',
        content: '[error: invalid subagent arguments]',
        details: { subagent_events: [] },
      } as never,
      agent_id: null,
      agent_name: null,
    })

    applyInjected('c1', 'retry differently', 's-sub-fallback')

    const toolMsgs = useMessageStore.getState().messages.c1.filter((m) => m.role === 'tool_result')
    expect(toolMsgs).toHaveLength(1)
    expect(toolMsgs[0]).toMatchObject({
      tool_call_id: 'call_sub_1',
      tool_name: 'subagent',
      run_id: 'run-1',
      content: [{ type: 'text', text: '[error: invalid subagent arguments]' }],
    })
  })

  it('dedupes subagent tool_result and ignores late subagent events after steer commit', () => {
    const apply = useMessageStore.getState().__applyEvent
    useMessageStore.setState({
      streamAgents: {},
      toolStartedMap: {},
      toolResultMap: {},
      currentRunId: 'run-1',
      isStreaming: true,
      streamingConversationId: 'c1',
    })
    apply({
      type: 'tool_call',
      timestamp: '2026-11-17T12:00:00.000Z',
      data: {
        tool_call_id: 'call_sub_2',
        name: 'subagent',
        arguments: { role: 'Reviewer', task: 'inspect diff' },
      },
      agent_id: null,
      agent_name: null,
    })
    apply({
      type: 'text_delta',
      timestamp: '2026-11-17T12:00:00.500Z',
      data: { content: 'subagent report' },
      agent_id: 'subagent:call_sub_2',
      agent_name: 'Reviewer',
    })
    apply({
      type: 'tool_result',
      timestamp: '2026-11-17T12:00:00.600Z',
      data: {
        tool_call_id: 'inner_tc_1',
        name: 'execute',
        content: 'running...',
      } as never,
      agent_id: 'subagent:call_sub_2',
      agent_name: 'Reviewer',
    })
    apply({
      type: 'tool_result',
      timestamp: '2026-11-17T12:00:00.800Z',
      data: {
        tool_call_id: 'inner_tc_1',
        name: 'execute',
        content: 'exit 0',
      } as never,
      agent_id: 'subagent:call_sub_2',
      agent_name: 'Reviewer',
    })
    apply({
      type: 'tool_result',
      timestamp: '2026-11-17T12:00:01.000Z',
      data: {
        tool_call_id: 'call_sub_2',
        name: 'subagent',
        content: 'subagent report',
      } as never,
      agent_id: null,
      agent_name: null,
    })

    applyInjected('c1', 'continue', 's-sub-live')

    const preSteerTools = useMessageStore
      .getState()
      .messages.c1.filter((m) => m.role === 'tool_result')
    expect(preSteerTools).toHaveLength(1)
    expect(preSteerTools[0].metadata?.subagent_events?.tool_results).toEqual([
      expect.objectContaining({
        tool_call_id: 'inner_tc_1',
        tool_name: 'execute',
        content: 'exit 0',
      }),
    ])

    // Late async drain from _drain_subagent_citation_queue lands after steer commit.
    apply({
      type: 'text_delta',
      timestamp: '2026-11-17T12:00:01.200Z',
      data: { content: ' trailing' },
      agent_id: 'subagent:call_sub_2',
      agent_name: 'Reviewer',
    })
    apply({
      type: 'text_delta',
      timestamp: '2026-11-17T12:00:01.500Z',
      data: { content: 'post-steer text' },
      agent_id: null,
      agent_name: null,
    })
    applyInjected('c1', 'second steer', 's-sub-live-2')

    const allSubTools = useMessageStore
      .getState()
      .messages.c1.filter((m) => m.role === 'tool_result' && m.tool_call_id === 'call_sub_2')
    expect(allSubTools).toHaveLength(1)
  })

  it('does not steal or wipe another conversation stream when called off-owner', () => {
    useMessageStore.setState({
      streamingConversationId: 'other-conv',
      currentRunId: 'run-other',
      streamAgents: {
        main: {
          text: 'other conv text',
          toolCalls: [],
          toolResults: [],
          thinking: '',
          blocks: [{ type: 'text', text: 'other conv text' }],
          name: null,
        },
      },
    })

    applyInjected('c1', 'do X', 's-off-owner')

    const s = useMessageStore.getState()
    expect(s.messages.c1.map((m) => m.role)).toEqual(['user', 'user'])
    expect(s.streamAgents.main.text).toBe('other conv text')
  })
})
