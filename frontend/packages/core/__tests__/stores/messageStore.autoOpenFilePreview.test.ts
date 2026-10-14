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

function deltaEvent(
  id: string | null,
  index: number,
  argsDelta = '',
  name: string | null = 'write',
): AgentEvent {
  return {
    type: 'tool_call_delta',
    timestamp: TS,
    agent_id: null,
    agent_name: null,
    data: { tool_call_id: id, index, name, args_delta: argsDelta },
  }
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

  it('keeps the first file selected when another call arrives', () => {
    useMessageStore.getState().__applyEvent(toolCallEvent('edit', { file_path: '/workspace/a.md' }))
    useMessageStore
      .getState()
      .__applyEvent(toolCallEvent('write', { file_path: '/workspace/b.md' }, 'tc-write-2'))
    const view = usePanelStore.getState().view
    expect(view.type).toBe('tool')
    if (view.type === 'tool') {
      expect(view.contentType).toBe('edit')
      expect(view.toolArgs.file_path).toBe('/workspace/a.md')
    }
  })

  it('opens before arguments arrive and keeps parallel streams separate without switching', () => {
    const apply = useMessageStore.getState().__applyEvent
    apply(deltaEvent('a', 0))
    expect(usePanelStore.getState().view).toMatchObject({
      type: 'tool',
      toolRef: { tool_call_id: 'a' },
      toolArgs: {},
    })
    apply(deltaEvent('b', 1, '{"content":"B'))
    apply(deltaEvent('a', 0, '{"content":"A'))
    apply(deltaEvent('b', 1, ' body"}'))
    apply(deltaEvent('a', 0, ' body"}'))
    expect(useMessageStore.getState().streamAgents.main.blocks).toMatchObject([
      { tool_call_id: 'a', args_text: '{"content":"A body"}' },
      { tool_call_id: 'b', args_text: '{"content":"B body"}' },
    ])
    expect(usePanelStore.getState().view).toMatchObject({
      type: 'tool',
      toolRef: { tool_call_id: 'a' },
    })
  })

  it('does not replace a user-selected write when a sibling streams or completes', () => {
    const apply = useMessageStore.getState().__applyEvent
    apply(deltaEvent('a', 0))
    apply(deltaEvent('b', 1))
    usePanelStore.getState().openTool('write', {}, null, 'write', {
      agent_id: null,
      tool_call_id: 'b',
      index: 1,
    })
    apply(deltaEvent('a', 0, '{"content":"A"}'))
    apply(toolCallEvent('write', { content: 'A' }, 'a'))
    apply(toolCallEvent('write', { content: 'B' }, 'b'))
    expect(usePanelStore.getState().view).toMatchObject({
      type: 'tool',
      source: 'user',
      toolRef: { tool_call_id: 'b' },
      toolArgs: { content: 'B' },
    })
  })

  it('fills in a late tool name so buffered content can be previewed', () => {
    const apply = useMessageStore.getState().__applyEvent
    apply(deltaEvent(null, 0, '{"content":"early', null))
    apply(deltaEvent('a', 0, ' text"}', 'write'))
    expect(useMessageStore.getState().streamAgents.main.blocks).toMatchObject([
      { name: 'write', tool_call_id: 'a', args_text: '{"content":"early text"}' },
    ])
    expect(usePanelStore.getState().view).toMatchObject({
      type: 'tool',
      contentType: 'write',
      toolRef: { tool_call_id: 'a' },
    })
  })

  it('binds a late call ID so a reused index cannot redirect the selected preview', () => {
    const apply = useMessageStore.getState().__applyEvent
    apply(deltaEvent(null, 0))
    apply(deltaEvent('a', 0, '{"content":"A"}'))
    apply(deltaEvent('b', 0, '{"content":"B"}'))
    expect(usePanelStore.getState().view).toMatchObject({
      type: 'tool',
      toolRef: { tool_call_id: 'a' },
    })
    expect(useMessageStore.getState().streamAgents.main.blocks).toMatchObject([
      { tool_call_id: 'a', args_text: '{"content":"A"}' },
      { tool_call_id: 'b', args_text: '{"content":"B"}' },
    ])
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
