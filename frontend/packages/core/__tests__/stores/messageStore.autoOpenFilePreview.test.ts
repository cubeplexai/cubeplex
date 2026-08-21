import { beforeEach, describe, expect, it } from 'vitest'

import type { AgentEvent } from '../../src/types'
import { useConversationStore } from '../../src/stores/conversationStore'
import { useMessageStore } from '../../src/stores/messageStore'
import { usePanelStore } from '../../src/stores/panelStore'

const CONV = 'conv-file-auto-open'
const TS = new Date().toISOString()

function toolCallEvent(name: string, args: Record<string, unknown>, id = 'tc-write-1'): AgentEvent {
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

describe('messageStore — auto-open write/edit file preview', () => {
  beforeEach(reset)

  it('opens the write preview when the agent starts a write call', () => {
    useMessageStore
      .getState()
      .__applyEvent(toolCallEvent('write', { file_path: '/workspace/a.md', content: '# hi' }))
    const view = usePanelStore.getState().view
    expect(view.type).toBe('tool')
    if (view.type === 'tool') {
      expect(view.contentType).toBe('write')
      expect(view.source).toBe('auto')
      expect(view.toolArgs.file_path).toBe('/workspace/a.md')
    }
  })

  it('opens the edit preview when the agent starts an edit call', () => {
    useMessageStore.getState().__applyEvent(
      toolCallEvent('edit', {
        file_path: '/workspace/a.md',
        edits: [{ old_string: 'a', new_string: 'b' }],
      }),
    )
    const view = usePanelStore.getState().view
    expect(view.type).toBe('tool')
    if (view.type === 'tool') {
      expect(view.contentType).toBe('edit')
      expect(view.source).toBe('auto')
    }
  })

  it('follows a later write after an earlier auto-opened edit', () => {
    useMessageStore.getState().__applyEvent(toolCallEvent('edit', { file_path: '/workspace/a.md' }))
    useMessageStore
      .getState()
      .__applyEvent(toolCallEvent('write', { file_path: '/workspace/b.md' }, 'tc-write-2'))
    const view = usePanelStore.getState().view
    expect(view.type).toBe('tool')
    if (view.type === 'tool') {
      expect(view.contentType).toBe('write')
      expect(view.toolArgs.file_path).toBe('/workspace/b.md')
    }
  })

  it('does not reopen the same tool call', () => {
    useMessageStore
      .getState()
      .__applyEvent(toolCallEvent('write', { file_path: '/workspace/a.md' }))
    const before = usePanelStore.getState().view
    const beforeKey = before.type === 'tool' ? before.highlightKey : undefined
    useMessageStore
      .getState()
      .__applyEvent(toolCallEvent('write', { file_path: '/workspace/a.md' }))
    const after = usePanelStore.getState().view
    expect(after.type).toBe('tool')
    if (after.type === 'tool') {
      expect(after.highlightKey).toBe(beforeKey)
    }
  })

  it('does not steal a user-picked artifact panel', () => {
    usePanelStore.getState().openArtifact(CONV, 'art-1', 'user')
    useMessageStore
      .getState()
      .__applyEvent(toolCallEvent('write', { file_path: '/workspace/a.md' }))
    const view = usePanelStore.getState().view
    expect(view.type).toBe('artifact')
    if (view.type === 'artifact') {
      expect(view.artifactId).toBe('art-1')
      expect(view.source).toBe('user')
    }
  })

  it('does not open when the chat surface is on a different conversation', () => {
    useConversationStore.setState({ viewingConversationId: 'conv-other' })
    useMessageStore
      .getState()
      .__applyEvent(toolCallEvent('write', { file_path: '/workspace/a.md' }))
    expect(usePanelStore.getState().view.type).toBe('closed')
  })

  it('does not open for unrelated tools', () => {
    useMessageStore.getState().__applyEvent(toolCallEvent('execute', { command: 'ls' }))
    expect(usePanelStore.getState().view.type).toBe('closed')
  })
})
