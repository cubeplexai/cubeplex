import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { BackgroundTask, BackgroundTaskEvent } from '../../src/types'

vi.mock('../../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api')>()
  return {
    ...actual,
    listBackgroundTasks: vi.fn(),
    listBackgroundTaskEvents: vi.fn(),
    getConversationBootstrap: vi.fn(),
    stopBackgroundTask: vi.fn(),
    stopAllConversationWork: vi.fn(),
  }
})

import {
  getConversationBootstrap,
  listBackgroundTaskEvents,
  listBackgroundTasks,
  stopAllConversationWork,
  stopBackgroundTask,
} from '../../src/api'
import { useMessageStore } from '../../src/stores/messageStore'

const fakeClient = {} as never

function task(overrides: Partial<BackgroundTask> = {}): BackgroundTask {
  return {
    id: 'bgt-1',
    kind: 'command',
    description: 'Build project',
    parent_task_id: null,
    originating_run_id: 'run-1',
    tool_call_id: 'tool-1',
    agent_id: null,
    execution_generation: 4,
    state: 'succeeded',
    deadline_at: null,
    stop_requested_at: null,
    stop_reason: null,
    backgrounded_at: '2026-09-22T00:00:00+00:00',
    finished_at: '2026-09-22T00:01:00+00:00',
    result_summary: 'done',
    result_ref: '/workspace/result.log',
    result_readiness: 'ready',
    result_unavailable_reason: null,
    revision: 2,
    created_at: '2026-09-22T00:00:00+00:00',
    updated_at: '2026-09-22T00:01:00+00:00',
    cleanup_pending: false,
    capabilities: {
      can_stop: true,
      remote_cancel_supported: false,
      reconnect_supported: true,
      logs_supported: true,
      input_supported: false,
    },
    notification: { enabled: true, has_pending: true, cancelled_at: null },
    details: null,
    ...overrides,
  }
}

function event(overrides: Partial<BackgroundTaskEvent> = {}): BackgroundTaskEvent {
  return {
    id: 'bge-1',
    task_id: 'bgt-1',
    task_kind: 'command',
    execution_generation: 4,
    reason: 'completion',
    summary: 'done',
    result_ref: '/workspace/result.log',
    state: 'pending',
    discard_reason: null,
    revision: 1,
    created_at: '2026-09-22T00:01:00+00:00',
    updated_at: '2026-09-22T00:01:00+00:00',
    delivered_at: null,
    ...overrides,
  }
}

describe('messageStore background task state', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useMessageStore.setState({
      backgroundTasks: {},
      backgroundEvents: {},
      backgroundEventCursor: {},
      backgroundEventsHasMore: {},
      backgroundSummary: {},
      executionGeneration: {},
      stopAllStatus: {},
      runControl: {},
      refreshingBackground: {},
      backgroundRefreshError: {},
    })
  })

  it('discovers a terminal task through its event without mixing it into steering', async () => {
    vi.mocked(listBackgroundTaskEvents).mockResolvedValue({
      items: [event()],
      next_cursor: 'older',
      has_more: true,
    })
    vi.mocked(listBackgroundTasks).mockResolvedValueOnce([]).mockResolvedValueOnce([task()])

    await useMessageStore.getState().refreshBackground(fakeClient, 'conv-1')

    const state = useMessageStore.getState()
    expect(listBackgroundTaskEvents).toHaveBeenCalledWith(fakeClient, 'conv-1', {
      delivery: 'all',
    })
    expect(listBackgroundTasks).toHaveBeenLastCalledWith(fakeClient, 'conv-1', ['bgt-1'])
    expect(state.backgroundTasks['conv-1']).toEqual([task()])
    expect(state.backgroundEvents['conv-1']).toEqual([event()])
    expect(state.backgroundSummary['conv-1'].has_pending).toBe(true)
    expect(state.pendingSteers['conv-1']).toBeUndefined()
  })

  it('keeps the last confirmed snapshot when refresh fails', async () => {
    const known = task({ state: 'running', revision: 1 })
    useMessageStore.setState({
      backgroundTasks: { 'conv-1': [known] },
      backgroundSummary: {
        'conv-1': {
          has_inflight: true,
          has_pending: false,
          has_cleanup: false,
          can_stop: true,
        },
      },
    })
    vi.mocked(listBackgroundTasks).mockRejectedValue(new Error('offline'))
    vi.mocked(listBackgroundTaskEvents).mockRejectedValue(new Error('offline'))

    await expect(
      useMessageStore.getState().refreshBackground(fakeClient, 'conv-1'),
    ).rejects.toThrow('offline')

    const state = useMessageStore.getState()
    expect(state.backgroundTasks['conv-1']).toEqual([known])
    expect(state.backgroundSummary['conv-1'].has_inflight).toBe(true)
    expect(state.backgroundRefreshError['conv-1']).toBe('offline')
  })

  it('does not let an older polling response overwrite an accepted Stop', async () => {
    const acceptedStop = task({
      state: 'running',
      revision: 5,
      stop_requested_at: '2026-09-22T00:02:00+00:00',
      cleanup_pending: true,
      capabilities: {
        ...task().capabilities,
        can_stop: false,
      },
    })
    const stale = task({ state: 'running', revision: 1, stop_requested_at: null })
    useMessageStore.setState({ backgroundTasks: { 'conv-1': [acceptedStop] } })
    vi.mocked(listBackgroundTasks).mockResolvedValue([stale])
    vi.mocked(listBackgroundTaskEvents).mockResolvedValue({
      items: [],
      next_cursor: null,
      has_more: false,
    })

    await useMessageStore.getState().refreshBackground(fakeClient, 'conv-1')

    expect(useMessageStore.getState().backgroundTasks['conv-1'][0]).toEqual(acceptedStop)
  })

  it('prioritizes newly discovered event tasks in the bounded detail query', async () => {
    const oldTasks = Array.from({ length: 100 }, (_, index) =>
      task({ id: `old-${index}`, tool_call_id: `tool-${index}` }),
    )
    const newestEvent = event({ task_id: 'new-task' })
    useMessageStore.setState({ backgroundTasks: { 'conv-1': oldTasks } })
    vi.mocked(listBackgroundTasks).mockResolvedValueOnce([]).mockResolvedValueOnce([])
    vi.mocked(listBackgroundTaskEvents).mockResolvedValue({
      items: [newestEvent],
      next_cursor: null,
      has_more: false,
    })

    await useMessageStore.getState().refreshBackground(fakeClient, 'conv-1')

    const detailIds = vi.mocked(listBackgroundTasks).mock.calls[1][2]
    expect(detailIds).toHaveLength(100)
    expect(detailIds?.[0]).toBe('new-task')
  })

  it('does not reset event pagination when polling refreshes the newest page', async () => {
    const newest = event({ id: 'event-newest', revision: 2 })
    const older = event({ id: 'event-older', created_at: '2026-09-21T23:00:00+00:00' })
    useMessageStore.setState({
      backgroundEvents: { 'conv-1': [newest, older] },
      backgroundEventCursor: { 'conv-1': 'deep-cursor' },
      backgroundEventsHasMore: { 'conv-1': true },
    })
    vi.mocked(listBackgroundTasks).mockResolvedValue([])
    vi.mocked(listBackgroundTaskEvents).mockResolvedValue({
      items: [newest],
      next_cursor: 'first-page-cursor',
      has_more: true,
    })

    await useMessageStore.getState().refreshBackground(fakeClient, 'conv-1')

    expect(useMessageStore.getState().backgroundEventCursor['conv-1']).toBe('deep-cursor')
    expect(useMessageStore.getState().backgroundEvents['conv-1']).toEqual(
      expect.arrayContaining([newest, older]),
    )
    expect(useMessageStore.getState().backgroundEvents['conv-1']).toHaveLength(2)
  })

  it('keeps fully loaded event history across a bootstrap baseline refresh', async () => {
    const newest = event({ id: 'event-newest', revision: 2 })
    const older = event({ id: 'event-older', created_at: '2026-09-21T23:00:00+00:00' })
    useMessageStore.setState({
      backgroundEvents: { 'conv-1': [newest, older] },
      backgroundEventCursor: { 'conv-1': null },
      backgroundEventsHasMore: { 'conv-1': false },
    })
    vi.mocked(getConversationBootstrap).mockResolvedValue({
      messages: [],
      oldest_seq: null,
      has_more: false,
      active_run: null,
      pending_hitl: null,
      pending_steers: [],
      todos: [],
      execution_generation: 4,
      stop_all: null,
      run_control: null,
      background_summary: {
        has_inflight: false,
        has_pending: false,
        has_cleanup: false,
        can_stop: false,
      },
      background_events: {
        items: [newest],
        next_cursor: 'first-page-cursor',
        has_more: true,
      },
    } as never)

    await useMessageStore.getState().loadMessages(fakeClient, 'conv-1', { force: true })

    expect(useMessageStore.getState().backgroundEvents['conv-1']).toEqual(
      expect.arrayContaining([newest, older]),
    )
    expect(useMessageStore.getState().backgroundEvents['conv-1']).toHaveLength(2)
    expect(useMessageStore.getState().backgroundEventsHasMore['conv-1']).toBe(false)
    expect(useMessageStore.getState().backgroundEventCursor['conv-1']).toBeNull()
  })

  it('does not let an older Stop response overwrite a newer task revision', async () => {
    const terminal = task({ revision: 8, state: 'cancelled' })
    const staleStop = task({ revision: 7, state: 'running', cleanup_pending: true })
    useMessageStore.setState({ backgroundTasks: { 'conv-1': [terminal] } })
    vi.mocked(stopBackgroundTask).mockResolvedValue({
      accepted: true,
      cleanup_pending: true,
      remote_cancel_supported: false,
      task: staleStop,
    })

    await useMessageStore.getState().stopTask(fakeClient, 'conv-1', 'bgt-1')

    expect(useMessageStore.getState().backgroundTasks['conv-1']).toEqual([terminal])
  })

  it('persists one-task and stop-all targets explicitly', async () => {
    const stopped = task({ stop_requested_at: '2026-09-22T00:02:00+00:00' })
    vi.mocked(stopBackgroundTask).mockResolvedValue({
      accepted: true,
      cleanup_pending: false,
      remote_cancel_supported: false,
      task: stopped,
    })
    vi.mocked(stopAllConversationWork).mockResolvedValue({
      execution_generation: 4,
      accepted: true,
      cleanup_pending: true,
    })
    vi.mocked(getConversationBootstrap).mockResolvedValue({ execution_generation: 4 } as never)
    useMessageStore.setState({
      backgroundTasks: { 'conv-1': [task()] },
      executionGeneration: { 'conv-1': 4 },
    })

    await useMessageStore.getState().stopTask(fakeClient, 'conv-1', 'bgt-1')
    await useMessageStore.getState().stopAllWork(fakeClient, 'conv-1')

    expect(stopBackgroundTask).toHaveBeenCalledWith(fakeClient, 'conv-1', 'bgt-1')
    expect(stopAllConversationWork).toHaveBeenCalledWith(fakeClient, 'conv-1', 4)
    expect(useMessageStore.getState().backgroundTasks['conv-1'][0]).toEqual(stopped)
    expect(useMessageStore.getState().stopAllStatus['conv-1']?.cleanup_pending).toBe(true)
  })

  it('refreshes the execution generation before Stop all', async () => {
    vi.mocked(getConversationBootstrap).mockResolvedValue({ execution_generation: 5 } as never)
    vi.mocked(stopAllConversationWork).mockResolvedValue({
      execution_generation: 5,
      accepted: true,
      cleanup_pending: false,
    })
    useMessageStore.setState({ executionGeneration: { 'conv-1': 4 } })

    await useMessageStore.getState().stopAllWork(fakeClient, 'conv-1')

    expect(stopAllConversationWork).toHaveBeenCalledWith(fakeClient, 'conv-1', 5)
    expect(useMessageStore.getState().executionGeneration['conv-1']).toBe(5)
  })
})
