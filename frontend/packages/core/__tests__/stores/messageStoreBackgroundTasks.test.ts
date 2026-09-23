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

  it('drops cached inflight rows missing from the authoritative inflight snapshot', async () => {
    const staleTasks = Array.from({ length: 101 }, (_, index) =>
      task({ id: `stale-${index}`, state: 'running', tool_call_id: `tool-${index}` }),
    )
    useMessageStore.setState({ backgroundTasks: { 'conv-1': staleTasks } })
    vi.mocked(listBackgroundTasks).mockResolvedValueOnce([]).mockResolvedValueOnce([])
    vi.mocked(listBackgroundTaskEvents).mockResolvedValue({
      items: [],
      next_cursor: null,
      has_more: false,
    })

    await useMessageStore.getState().refreshBackground(fakeClient, 'conv-1')

    expect(useMessageStore.getState().backgroundTasks['conv-1']).toEqual([])
  })

  it('prunes stale actionable controls beyond the task detail query limit', async () => {
    const staleTasks = Array.from({ length: 101 }, (_, index) =>
      task({
        id: `stale-${index}`,
        tool_call_id: `tool-${index}`,
        capabilities: { ...task().capabilities, can_stop: true },
        notification: { enabled: true, has_pending: true, cancelled_at: null },
      }),
    )
    useMessageStore.setState({ backgroundTasks: { 'conv-1': staleTasks } })
    vi.mocked(listBackgroundTasks).mockResolvedValueOnce([]).mockResolvedValueOnce([])
    vi.mocked(listBackgroundTaskEvents).mockResolvedValue({
      items: [],
      next_cursor: null,
      has_more: false,
    })

    await useMessageStore.getState().refreshBackground(fakeClient, 'conv-1')

    expect(useMessageStore.getState().backgroundTasks['conv-1']).toEqual([])
  })

  it('clears authoritative cleanup while preserving pending controls from a partial event page', async () => {
    useMessageStore.setState({
      backgroundSummary: {
        'conv-1': {
          has_inflight: false,
          has_pending: true,
          has_cleanup: true,
          can_stop: true,
        },
      },
    })
    vi.mocked(listBackgroundTasks).mockResolvedValue([])
    vi.mocked(listBackgroundTaskEvents).mockResolvedValue({
      items: [],
      next_cursor: 'older-events',
      has_more: true,
    })

    await useMessageStore.getState().refreshBackground(fakeClient, 'conv-1')

    expect(useMessageStore.getState().backgroundSummary['conv-1']).toEqual({
      has_inflight: false,
      has_pending: true,
      has_cleanup: false,
      can_stop: true,
    })
  })

  it('clears cleanup and stopability after the known task settles', async () => {
    const cleaning = task({
      state: 'running',
      revision: 2,
      cleanup_pending: true,
      capabilities: { ...task().capabilities, can_stop: true },
    })
    const settled = task({
      state: 'succeeded',
      revision: 3,
      cleanup_pending: false,
      capabilities: { ...task().capabilities, can_stop: false },
    })
    useMessageStore.setState({
      backgroundTasks: { 'conv-1': [cleaning] },
      backgroundSummary: {
        'conv-1': {
          has_inflight: true,
          has_pending: false,
          has_cleanup: true,
          can_stop: true,
        },
      },
    })
    vi.mocked(listBackgroundTasks).mockResolvedValueOnce([]).mockResolvedValueOnce([settled])
    vi.mocked(listBackgroundTaskEvents).mockResolvedValue({
      items: [],
      next_cursor: null,
      has_more: false,
    })

    await useMessageStore.getState().refreshBackground(fakeClient, 'conv-1')

    expect(useMessageStore.getState().backgroundSummary['conv-1']).toEqual({
      has_inflight: false,
      has_pending: false,
      has_cleanup: false,
      can_stop: false,
    })
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

  it('refreshes a loaded older event until its terminal revision arrives', async () => {
    const newest = event({ id: 'event-newest', state: 'delivered', revision: 2 })
    const olderPending = event({
      id: 'event-older',
      state: 'pending',
      created_at: '2026-09-21T23:00:00+00:00',
    })
    const olderDelivered = event({ ...olderPending, state: 'delivered', revision: 3 })
    useMessageStore.setState({
      backgroundEvents: { 'conv-1': [newest, olderPending] },
      backgroundEventCursor: { 'conv-1': null },
      backgroundEventsHasMore: { 'conv-1': false },
    })
    vi.mocked(listBackgroundTasks).mockResolvedValue([])
    vi.mocked(listBackgroundTaskEvents)
      .mockResolvedValueOnce({
        items: [newest],
        next_cursor: 'older-page',
        has_more: true,
      })
      .mockResolvedValueOnce({
        items: [olderDelivered],
        next_cursor: null,
        has_more: false,
      })

    await useMessageStore.getState().refreshBackground(fakeClient, 'conv-1')

    expect(listBackgroundTaskEvents).toHaveBeenLastCalledWith(fakeClient, 'conv-1', {
      delivery: 'all',
      cursor: 'older-page',
      limit: 50,
    })
    expect(
      useMessageStore.getState().backgroundEvents['conv-1'].find(({ id }) => id === 'event-older'),
    ).toEqual(olderDelivered)
  })

  it('restarts pagination from the newest page when an event gap is detected', async () => {
    const cached = event({ id: 'event-cached' })
    const newest = event({ id: 'event-newest', revision: 2 })
    useMessageStore.setState({
      backgroundEvents: { 'conv-1': [cached] },
      backgroundEventCursor: { 'conv-1': 'deep-cursor' },
      backgroundEventsHasMore: { 'conv-1': true },
    })
    vi.mocked(listBackgroundTasks).mockResolvedValue([])
    vi.mocked(listBackgroundTaskEvents).mockResolvedValue({
      items: [newest],
      next_cursor: 'gap-cursor',
      has_more: true,
    })

    await useMessageStore.getState().refreshBackground(fakeClient, 'conv-1')

    expect(useMessageStore.getState().backgroundEventCursor['conv-1']).toBe('gap-cursor')
    expect(useMessageStore.getState().backgroundEventsHasMore['conv-1']).toBe(true)
  })

  it('does not let an older page response overwrite a refreshed gap cursor', async () => {
    const cached = event({ id: 'event-cached' })
    useMessageStore.setState({
      backgroundEvents: { 'conv-1': [cached] },
      backgroundEventCursor: { 'conv-1': 'old-cursor' },
      backgroundEventsHasMore: { 'conv-1': true },
    })
    let resolvePage: ((page: Awaited<ReturnType<typeof listBackgroundTaskEvents>>) => void) | null =
      null
    vi.mocked(listBackgroundTaskEvents).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolvePage = resolve
        }),
    )
    vi.mocked(listBackgroundTasks).mockResolvedValue([])

    const loading = useMessageStore.getState().loadMoreBackgroundEvents(fakeClient, 'conv-1')
    useMessageStore.setState({
      backgroundEventCursor: { 'conv-1': 'gap-cursor' },
      backgroundEventsHasMore: { 'conv-1': true },
    })
    resolvePage?.({ items: [], next_cursor: 'older-cursor', has_more: true })
    await loading

    expect(useMessageStore.getState().backgroundEventCursor['conv-1']).toBe('gap-cursor')
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

  it('preserves an expanded message window during a bootstrap baseline refresh', async () => {
    const older = {
      id: 'message-older',
      seq: 1,
      role: 'user' as const,
      content: [{ type: 'text' as const, text: 'older' }],
    }
    const newest = {
      id: 'message-newest',
      seq: 400,
      role: 'assistant' as const,
      content: [{ type: 'text' as const, text: 'newest' }],
    }
    useMessageStore.setState({
      messages: { 'conv-1': [older, newest] },
      oldestSeqByConv: { 'conv-1': 1 },
      hasMoreByConv: { 'conv-1': true },
    })
    vi.mocked(getConversationBootstrap).mockResolvedValue({
      messages: [newest],
      oldest_seq: 350,
      has_more: true,
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
      background_events: { items: [], next_cursor: null, has_more: false },
    } as never)

    await useMessageStore.getState().loadMessages(fakeClient, 'conv-1', {
      preserveLoadedHistory: true,
    })

    expect(useMessageStore.getState().messages['conv-1']).toEqual([older, newest])
    expect(useMessageStore.getState().oldestSeqByConv['conv-1']).toBe(1)
    expect(useMessageStore.getState().hasMoreByConv['conv-1']).toBe(true)
  })

  it('replaces optimistic turn messages with their persisted history rows', async () => {
    const older = {
      id: 'message-older',
      seq: 1,
      role: 'user' as const,
      content: [{ type: 'text' as const, text: 'older' }],
    }
    const optimisticAssistant = {
      id: 'assistant-temp',
      role: 'assistant' as const,
      run_id: 'run-1',
      content: [{ type: 'text' as const, text: 'done' }],
    }
    const optimisticTool = {
      id: 'tool-temp',
      role: 'tool_result' as const,
      run_id: 'run-1',
      tool_call_id: 'tool-1',
      tool_name: 'sandbox',
      content: [{ type: 'text' as const, text: 'output' }],
    }
    const persistedAssistant = { ...optimisticAssistant, id: 'message-assistant', seq: 10 }
    const persistedTool = { ...optimisticTool, id: 'message-tool', seq: 11 }
    useMessageStore.setState({
      messages: { 'conv-1': [older, optimisticAssistant, optimisticTool] },
      oldestSeqByConv: { 'conv-1': 1 },
      hasMoreByConv: { 'conv-1': false },
    })
    vi.mocked(getConversationBootstrap).mockResolvedValue({
      messages: [persistedAssistant, persistedTool],
      oldest_seq: 10,
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
      background_events: { items: [], next_cursor: null, has_more: false },
    } as never)

    await useMessageStore.getState().loadMessages(fakeClient, 'conv-1', {
      preserveLoadedHistory: true,
    })

    expect(useMessageStore.getState().messages['conv-1']).toEqual([
      older,
      persistedAssistant,
      persistedTool,
    ])
  })

  it('keeps a message appended during bootstrap after the persisted tail', async () => {
    const older = {
      id: 'message-older',
      seq: 1,
      role: 'user' as const,
      content: [{ type: 'text' as const, text: 'older' }],
    }
    const persisted = {
      id: 'message-persisted',
      seq: 10,
      role: 'assistant' as const,
      content: [{ type: 'text' as const, text: 'persisted' }],
    }
    const appended = {
      id: 'message-appended',
      seq: 11,
      role: 'assistant' as const,
      content: [{ type: 'text' as const, text: 'appended while loading' }],
    }
    useMessageStore.setState({
      messages: { 'conv-1': [older] },
      oldestSeqByConv: { 'conv-1': 1 },
      hasMoreByConv: { 'conv-1': false },
    })
    let resolveBootstrap: ((value: unknown) => void) | null = null
    vi.mocked(getConversationBootstrap).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveBootstrap = resolve
        }) as never,
    )

    const loading = useMessageStore.getState().loadMessages(fakeClient, 'conv-1', {
      preserveLoadedHistory: true,
    })
    useMessageStore.setState({ messages: { 'conv-1': [older, appended] } })
    resolveBootstrap?.({
      messages: [persisted],
      oldest_seq: 10,
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
      background_events: { items: [], next_cursor: null, has_more: false },
    })
    await loading

    expect(useMessageStore.getState().messages['conv-1']).toEqual([older, persisted, appended])
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
    expect(useMessageStore.getState().stopAllStatus['conv-1']).toEqual({
      execution_generation: 4,
      requested_at: expect.any(String),
      cleanup_pending: true,
    })
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
