import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AgentEvent, ArtifactEventData } from '../../src/types'

vi.mock('../../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api')>()
  return {
    ...actual,
    streamMessages: vi.fn(),
  }
})

import { streamMessages } from '../../src/api'
import { useArtifactStore } from '../../src/stores/artifactStore'
import { useConversationStore } from '../../src/stores/conversationStore'
import { useMessageStore } from '../../src/stores/messageStore'
import { usePanelStore } from '../../src/stores/panelStore'

const CONV = 'conv-artifact-auto-open'
const NOW = new Date().toISOString()
const fakeClient = { resolvePath: (path: string) => path } as never

function event(type: AgentEvent['type'], eventId: string, data: unknown): AgentEvent {
  return {
    type,
    event_id: eventId,
    timestamp: NOW,
    agent_id: null,
    agent_name: null,
    data,
  } as AgentEvent
}

function artifact(id: string): ArtifactEventData['artifact'] {
  return {
    id,
    conversation_id: CONV,
    name: 'Report',
    artifact_type: 'document',
    path: '/workspace/report.md',
    mime_type: 'text/markdown',
    created_at: NOW,
    updated_at: NOW,
    version: 1,
  }
}

function streamWith(events: AgentEvent[]): void {
  vi.mocked(streamMessages).mockImplementation(async function* (_c, _id, _text, _a, _s, opts) {
    opts?.onRunId?.('run-1')
    for (const item of events) yield item
    yield event('done', '99-0', {})
  })
}

async function send(): Promise<void> {
  await useMessageStore.getState().send(fakeClient, CONV, 'make a report')
}

describe('messageStore — auto-open artifact preview', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    useMessageStore.getState().clearStream()
    useMessageStore.setState({
      messages: {
        [CONV]: [
          {
            id: 'prior-message',
            role: 'user',
            content: [{ type: 'text', text: 'prior' }],
            timestamp: Date.now() / 1000,
            metadata: {},
          },
        ],
      },
      errors: {},
      runLifecycle: {},
    })
    useConversationStore.setState({ viewingConversationId: CONV })
    useArtifactStore.setState({ artifacts: {} })
    usePanelStore.setState({ view: { type: 'closed' } })
  })

  it('replaces an auto-opened write preview after the artifact is saved', async () => {
    streamWith([
      event('tool_call', '1-0', {
        tool_call_id: 'write-1',
        name: 'write',
        arguments: { file_path: '/workspace/report.md', content: '# Report' },
      }),
      event('artifact', '2-0', { action: 'created', artifact: artifact('art-1') }),
    ])

    await send()

    expect(useArtifactStore.getState().getArtifacts(CONV)).toHaveLength(1)
    expect(usePanelStore.getState().view).toMatchObject({
      type: 'artifact',
      conversationId: CONV,
      artifactId: 'art-1',
      source: 'auto',
    })
  })

  it('replaces a user-selected artifact with the newly saved artifact', async () => {
    usePanelStore.getState().openArtifact(CONV, 'art-old', 'user')
    streamWith([event('artifact', '1-0', { action: 'created', artifact: artifact('art-new') })])

    await send()

    expect(usePanelStore.getState().view).toMatchObject({
      type: 'artifact',
      conversationId: CONV,
      artifactId: 'art-new',
      source: 'auto',
    })
  })
})
