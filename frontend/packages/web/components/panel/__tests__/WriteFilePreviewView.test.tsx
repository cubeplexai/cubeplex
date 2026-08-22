import { act, render, screen } from '@testing-library/react'
import {
  useConversationStore,
  useMessageStore,
  usePanelStore,
  type AgentEvent,
} from '@cubeplex/core'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { WriteFilePreviewView } from '../WriteFilePreviewView'

vi.mock('@/hooks/useSandboxMarkdownContext', () => ({
  useSandboxMarkdownContext: () => null,
}))

class ResizeObserverStub {
  observe(): void {}
  disconnect(): void {}
}

vi.stubGlobal('ResizeObserver', ResizeObserverStub)

const CONVERSATION_ID = 'conv-write-preview'

function writeDelta(argsDelta: string, eventId: string, name: string | null = 'write'): AgentEvent {
  return {
    type: 'tool_call_delta',
    event_id: eventId,
    timestamp: new Date().toISOString(),
    agent_id: null,
    agent_name: null,
    data: {
      tool_call_id: 'tc-write-1',
      name,
      args_delta: argsDelta,
      index: 0,
    },
  }
}

function completedWriteCall(): AgentEvent {
  return {
    type: 'tool_call',
    event_id: '1-3',
    timestamp: new Date().toISOString(),
    agent_id: null,
    agent_name: null,
    data: {
      tool_call_id: 'tc-write-1',
      name: 'write',
      arguments: {
        file_path: '/workspace/notes.txt',
        content: 'streamed body',
      },
    },
  }
}

describe('WriteFilePreviewView', () => {
  beforeEach(() => {
    useMessageStore.setState({
      lastAppliedEventId: null,
      streamingConversationId: CONVERSATION_ID,
      streamAgents: {},
      toolStartedMap: {},
    })
    useConversationStore.setState({ viewingConversationId: CONVERSATION_ID })
    usePanelStore.setState({ view: { type: 'closed' } })
  })

  it('shows streamed write arguments after the main agent auto-opens the panel', () => {
    useMessageStore
      .getState()
      .__applyEvent(writeDelta('{"file_path":"/workspace/notes.txt","content":"stream', '1-1'))

    const view = usePanelStore.getState().view
    expect(view.type).toBe('tool')
    if (view.type !== 'tool') return

    const { rerender } = render(
      <WriteFilePreviewView args={view.toolArgs} result={view.toolResult} toolRef={view.toolRef} />,
    )

    expect(screen.getByText('/workspace/notes.txt')).toBeInTheDocument()
    expect(screen.getByText('stream')).toBeInTheDocument()

    act(() => {
      useMessageStore.getState().__applyEvent(writeDelta('ed body"}', '1-2', null))
    })

    expect(screen.getByText('streamed body')).toBeInTheDocument()

    act(() => {
      useMessageStore.getState().__applyEvent(completedWriteCall())
      useMessageStore.setState({ streamAgents: {} })
    })

    const completedView = usePanelStore.getState().view
    expect(completedView.type).toBe('tool')
    if (completedView.type !== 'tool') return
    rerender(
      <WriteFilePreviewView
        args={completedView.toolArgs}
        result={completedView.toolResult}
        toolRef={completedView.toolRef}
      />,
    )

    expect(screen.getByText('/workspace/notes.txt')).toBeInTheDocument()
    expect(screen.getByText('streamed body')).toBeInTheDocument()
    expect(screen.queryByText('Untitled file')).not.toBeInTheDocument()
  })
})
