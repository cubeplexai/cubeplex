import { beforeEach, describe, expect, it } from 'vitest'

import type { AgentEvent } from '../../src/types'
import { useConversationStore } from '../../src/stores/conversationStore'
import { useMessageStore } from '../../src/stores/messageStore'
import { usePanelStore } from '../../src/stores/panelStore'

const CONV = 'conv-browser-auto-open'
const TS = new Date().toISOString()

function toolCallEvent(
  name: string,
  args: Record<string, unknown>,
  id = 'tc-browser-1',
): AgentEvent {
  return {
    type: 'tool_call',
    event_id: `ev-${id}`,
    timestamp: TS,
    agent_id: null,
    agent_name: null,
    data: {
      tool_call_id: id,
      name,
      arguments: args,
    },
  } as unknown as AgentEvent
}

function reset(): void {
  useMessageStore.setState({
    lastAppliedEventId: null,
    streamingConversationId: CONV,
    streamAgents: {},
    toolStartedMap: {},
  })
  useConversationStore.setState({ viewingConversationId: CONV })
  usePanelStore.setState({ view: { type: 'closed' } })
}

describe('messageStore — auto-open sandbox browser preview', () => {
  beforeEach(reset)

  it('opens the sandbox browser panel when the agent loads the browser skill', () => {
    useMessageStore.getState().__applyEvent(toolCallEvent('load_skill', { skill_name: 'browser' }))
    const view = usePanelStore.getState().view
    expect(view.type).toBe('sandbox')
    if (view.type === 'sandbox') {
      expect(view.initialTab).toBe('browser')
    }
  })

  it('opens the sandbox browser panel when the agent runs agent-browser', () => {
    useMessageStore
      .getState()
      .__applyEvent(toolCallEvent('execute', { cmd: 'agent-browser snapshot' }))
    const view = usePanelStore.getState().view
    expect(view.type).toBe('sandbox')
    if (view.type === 'sandbox') {
      expect(view.initialTab).toBe('browser')
    }
  })

  it('does not steal a user-picked artifact panel', () => {
    usePanelStore.getState().openArtifact(CONV, 'art-1', 'user')
    useMessageStore.getState().__applyEvent(toolCallEvent('load_skill', { skill_name: 'browser' }))
    const view = usePanelStore.getState().view
    expect(view.type).toBe('artifact')
    if (view.type === 'artifact') {
      expect(view.artifactId).toBe('art-1')
      expect(view.source).toBe('user')
    }
  })

  it('does not open when the chat surface is on a different conversation', () => {
    useConversationStore.setState({ viewingConversationId: 'conv-other' })
    useMessageStore.getState().__applyEvent(toolCallEvent('load_skill', { skill_name: 'browser' }))
    expect(usePanelStore.getState().view.type).toBe('closed')
  })

  it('does not reopen when the browser sandbox tab is already showing', () => {
    usePanelStore.getState().openSandbox('browser')
    const before = usePanelStore.getState().view
    const beforeRevision = before.type === 'sandbox' ? before.revision : undefined
    useMessageStore
      .getState()
      .__applyEvent(toolCallEvent('execute', { cmd: 'agent-browser get url' }))
    const after = usePanelStore.getState().view
    expect(after.type).toBe('sandbox')
    if (after.type === 'sandbox') {
      expect(after.revision).toBe(beforeRevision)
    }
  })
})
