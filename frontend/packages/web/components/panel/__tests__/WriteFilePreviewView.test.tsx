import { act, fireEvent, render, screen } from '@testing-library/react'
import {
  useConversationStore,
  useMessageStore,
  usePanelStore,
  type AgentEvent,
} from '@cubeplex/core'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { WriteFilePreviewView } from '../WriteFilePreviewView'
import { resolveLiveWriteFile } from '@/lib/writeFilePreview'
import { ToolCallItem } from '@/components/chat/ToolCallItem'

vi.mock('@/hooks/useSandboxMarkdownContext', () => ({
  useSandboxMarkdownContext: () => null,
}))

class ResizeObserverStub {
  observe(): void {}
  disconnect(): void {}
}

vi.stubGlobal('ResizeObserver', ResizeObserverStub)

const CONVERSATION_ID = 'conv-write-preview'

function writeDelta(
  argsDelta: string,
  eventId: string,
  name: string | null = 'write',
  id = 'tc-write-1',
  index = 0,
): AgentEvent {
  return {
    type: 'tool_call_delta',
    event_id: eventId,
    timestamp: new Date().toISOString(),
    agent_id: null,
    agent_name: null,
    data: {
      tool_call_id: id,
      name,
      args_delta: argsDelta,
      index,
    },
  }
}

function SelectedWritePreview() {
  const view = usePanelStore((s) => s.view)
  const blocks = useMessageStore((s) => s.streamAgents.main?.blocks)
  return (
    <>
      {blocks?.map(
        (block) =>
          block.type === 'tool_call_streaming' && (
            <ToolCallItem
              key={block.index}
              name={block.name}
              arguments={{}}
              toolCallId={block.tool_call_id ?? ''}
              summaryOverride={`File ${block.index}`}
              contentTypeOverride="write"
              isPending
              allowOpenWhenPending
              toolRef={{ agent_id: null, tool_call_id: block.tool_call_id, index: block.index }}
            />
          ),
      )}
      {view.type === 'tool' && (
        <WriteFilePreviewView
          args={view.toolArgs}
          result={view.toolResult}
          toolRef={view.toolRef}
        />
      )}
    </>
  )
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

  it('streams parallel files independently and respects a user switch before completion', () => {
    render(<SelectedWritePreview />)
    const apply = useMessageStore.getState().__applyEvent
    act(() => apply(writeDelta('', '1-0')))
    expect(screen.getByRole('button', { name: /Write.*File 0/ })).toBeEnabled()
    expect(screen.getByText('Untitled file')).toBeInTheDocument()

    act(() => {
      apply(writeDelta('{"file_path":"/workspace/notes.txt","content":"first', '1-1'))
      apply(
        writeDelta(
          '{"file_path":"/workspace/other.txt","content":"second',
          '1-2',
          'write',
          'tc-write-2',
          1,
        ),
      )
    })
    expect(screen.getByText('/workspace/notes.txt')).toBeInTheDocument()
    expect(screen.getByText('first')).toBeInTheDocument()
    expect(screen.queryByText('second')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Write.*File 1/ }))
    act(() => {
      apply(writeDelta(' file"}', '1-3', null))
      apply(writeDelta(' file"}', '1-4', null, 'tc-write-2', 1))
      apply({ ...completedWriteCall(), event_id: '1-5' })
    })
    expect(screen.getByText('/workspace/other.txt')).toBeInTheDocument()
    expect(screen.getByText('second file')).toBeInTheDocument()
    expect(screen.queryByText('streamed body')).not.toBeInTheDocument()
  })

  it('uses the call ID rather than a reused index to resolve the preview', () => {
    const resolved = resolveLiveWriteFile(
      [
        {
          type: 'tool_call_streaming',
          name: 'write',
          tool_call_id: 'a',
          index: 0,
          args_text: '{"content":"selected"}',
        },
        {
          type: 'tool_call_streaming',
          name: 'write',
          tool_call_id: 'b',
          index: 0,
          args_text: '{"content":"other"}',
        },
      ],
      { agent_id: null, tool_call_id: 'a', index: 0 },
    )
    expect(resolved?.content).toBe('selected')
  })
})
