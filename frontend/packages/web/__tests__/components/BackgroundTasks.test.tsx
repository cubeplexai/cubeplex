import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  stopTask: vi.fn(),
  stopAllWork: vi.fn(),
  refreshBackground: vi.fn(),
  loadMessages: vi.fn(),
  setWorkspaceId: vi.fn(),
  openBackgroundTasks: vi.fn(),
  closePanel: vi.fn(),
  panel: { view: { type: 'closed' } } as Record<string, unknown>,
  toastError: vi.fn(),
  state: {} as Record<string, unknown>,
  selectorSnapshots: [] as unknown[],
}))

vi.mock('@cubeplex/core', () => {
  const useMessageStore = (selector: (state: Record<string, unknown>) => unknown) => {
    const selected = selector(mocks.state)
    mocks.selectorSnapshots.push(selected, selector(mocks.state))
    return selected
  }
  useMessageStore.getState = () => mocks.state
  const usePanelStore = (selector: (state: Record<string, unknown>) => unknown) =>
    selector(mocks.panel)
  return {
    createApiClient: () => ({ setWorkspaceId: mocks.setWorkspaceId }),
    useMessageStore,
    usePanelStore,
  }
})

vi.mock('@/hooks/useWorkspaceContext', () => ({
  useWorkspaceContext: () => ({ workspaceId: 'ws-1' }),
}))

vi.mock('sonner', () => ({ toast: { error: mocks.toastError } }))

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string, values?: { count?: number }) =>
    ({
      title: '后台任务',
      description: '独立继续执行',
      open: '后台任务',
      openWithCount: `后台任务（${values?.count} 项进行中）`,
      empty: '暂无后台任务',
      stopTask: '停止任务',
      stopAll: '全部停止',
      submittingStop: '正在提交…',
      stopping: '正在停止',
      stoppingAll: '正在停止全部工作',
      finalizing: '正在整理结果',
      refreshFailed: '刷新失败',
      unnamed: '后台任务',
      'states.running': '运行中',
      'states.succeeded': '已成功',
      'states.cancelled': '已取消',
    })[key] ?? key,
}))

import { BackgroundTasks, BackgroundTasksButton } from '@/components/layout/BackgroundTasks'

function runningTask() {
  return {
    id: 'bgt-1',
    description: 'Build project',
    created_at: '2026-09-22T00:00:00+00:00',
    result_summary: '',
    execution_generation: 4,
    state: 'running',
    stop_requested_at: null,
    cleanup_pending: false,
    notification: { has_pending: false },
    capabilities: { can_stop: true },
  }
}

describe('BackgroundTasks', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    mocks.selectorSnapshots = []
    mocks.stopTask.mockResolvedValue(undefined)
    mocks.stopAllWork.mockResolvedValue(undefined)
    mocks.refreshBackground.mockResolvedValue(undefined)
    mocks.toastError.mockReset()
    mocks.panel = {
      view: { type: 'closed' },
      openBackgroundTasks: mocks.openBackgroundTasks,
      close: mocks.closePanel,
    }
    mocks.state = {
      backgroundTasks: { 'conv-1': [runningTask()] },
      backgroundSummary: {
        'conv-1': {
          has_inflight: true,
          has_pending: false,
          has_cleanup: false,
          can_stop: true,
        },
      },
      stopAllStatus: { 'conv-1': null },
      runControl: { 'conv-1': null },
      backgroundRefreshError: { 'conv-1': null },
      executionGeneration: { 'conv-1': 4 },
      refreshBackground: mocks.refreshBackground,
      loadMessages: mocks.loadMessages,
      stopTask: mocks.stopTask,
      stopAllWork: mocks.stopAllWork,
      streamingConversationId: null,
      currentRunId: null,
    }
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('keeps task controls separate from user steering', () => {
    render(<BackgroundTasks conversationId="conv-1" />)

    expect(screen.getByText('Build project')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '停止任务' }))
    fireEvent.click(screen.getByRole('button', { name: '全部停止' }))

    expect(mocks.stopTask).toHaveBeenCalledWith(expect.anything(), 'conv-1', 'bgt-1')
    expect(mocks.stopAllWork).toHaveBeenCalledWith(expect.anything(), 'conv-1')
  })

  it('does not report an accepted Stop all as failed when its refresh fails', async () => {
    mocks.refreshBackground.mockRejectedValueOnce(new Error('refresh unavailable'))
    render(<BackgroundTasks conversationId="conv-1" />)

    fireEvent.click(screen.getByRole('button', { name: '全部停止' }))
    await vi.advanceTimersByTimeAsync(0)

    expect(mocks.stopAllWork).toHaveBeenCalledWith(expect.anything(), 'conv-1')
    expect(mocks.toastError).not.toHaveBeenCalled()
  })

  it('keeps the cold-load task selector snapshot stable', () => {
    mocks.state = {
      ...mocks.state,
      backgroundTasks: {},
      backgroundSummary: {},
      stopAllStatus: {},
      runControl: {},
      backgroundRefreshError: {},
    }

    render(<BackgroundTasks conversationId="conv-1" />)

    const taskSnapshots = mocks.selectorSnapshots.filter(
      (snapshot): snapshot is { tasks: unknown[] } =>
        typeof snapshot === 'object' && snapshot !== null && 'tasks' in snapshot,
    )
    expect(taskSnapshots).toHaveLength(2)
    expect(taskSnapshots[1]).toBe(taskSnapshots[0])
  })

  it('shows accepted Stop as pending confirmation instead of hiding the task', () => {
    mocks.state = {
      ...mocks.state,
      backgroundTasks: {
        'conv-1': [
          {
            ...runningTask(),
            stop_requested_at: '2026-09-22T00:00:00+00:00',
            cleanup_pending: true,
            capabilities: { can_stop: false },
          },
        ],
      },
    }

    render(<BackgroundTasks conversationId="conv-1" />)

    expect(screen.getByText('Build project')).toBeInTheDocument()
    expect(screen.getByText('正在停止')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '停止任务' })).not.toBeInTheDocument()
  })

  it('shows the terminal state after a stopped task has finished', () => {
    mocks.state = {
      ...mocks.state,
      backgroundTasks: {
        'conv-1': [
          {
            ...runningTask(),
            state: 'cancelled',
            stop_requested_at: '2026-09-22T00:00:00+00:00',
            capabilities: { can_stop: false },
          },
        ],
      },
    }

    render(<BackgroundTasks conversationId="conv-1" />)

    expect(screen.getByText('已取消')).toBeInTheDocument()
    expect(screen.queryByText('正在停止')).not.toBeInTheDocument()
  })

  it('shows an inflight command as running while its log is still open', () => {
    mocks.state = {
      ...mocks.state,
      backgroundTasks: {
        'conv-1': [{ ...runningTask(), cleanup_pending: true }],
      },
    }

    render(<BackgroundTasks conversationId="conv-1" />)

    expect(screen.getByText('运行中')).toBeInTheDocument()
    expect(screen.queryByText('正在整理结果')).not.toBeInTheDocument()
  })

  it('keeps Stop-all cleanup visible after every task becomes terminal', () => {
    mocks.state = {
      ...mocks.state,
      backgroundTasks: { 'conv-1': [] },
      backgroundSummary: {
        'conv-1': {
          has_inflight: false,
          has_pending: false,
          has_cleanup: false,
          can_stop: false,
        },
      },
      stopAllStatus: {
        'conv-1': {
          execution_generation: 4,
          requested_at: '2026-09-22T00:00:00+00:00',
          cleanup_pending: true,
        },
      },
    }

    render(<BackgroundTasks conversationId="conv-1" />)

    expect(screen.getByText('正在停止全部工作')).toBeInTheDocument()
  })

  it('does not present a foreground-only run as background work', () => {
    mocks.state = {
      ...mocks.state,
      backgroundTasks: { 'conv-1': [] },
      backgroundSummary: {
        'conv-1': {
          has_inflight: false,
          has_pending: false,
          has_cleanup: false,
          can_stop: false,
        },
      },
      runControl: {
        'conv-1': {
          run_id: 'run-1',
          stop_requested_at: null,
          cleanup_pending: false,
          can_stop: true,
        },
      },
    }

    render(<BackgroundTasksButton conversationId="conv-1" />)

    expect(screen.getByRole('button', { name: '后台任务' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '全部停止' })).not.toBeInTheDocument()
  })

  it('does not present a locally admitted foreground run as background work', () => {
    mocks.state = {
      ...mocks.state,
      backgroundTasks: { 'conv-1': [] },
      backgroundSummary: {
        'conv-1': {
          has_inflight: false,
          has_pending: false,
          has_cleanup: false,
          can_stop: false,
        },
      },
      runControl: { 'conv-1': null },
      streamingConversationId: 'conv-1',
      currentRunId: 'run-local',
    }

    render(<BackgroundTasksButton conversationId="conv-1" />)

    expect(screen.getByRole('button', { name: '后台任务' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '全部停止' })).not.toBeInTheDocument()
  })

  it('keeps a completed task available in the task detail view', () => {
    mocks.state = {
      ...mocks.state,
      backgroundTasks: {
        'conv-1': [
          {
            ...runningTask(),
            state: 'succeeded',
            capabilities: { can_stop: false },
          },
        ],
      },
      backgroundSummary: {
        'conv-1': {
          has_inflight: false,
          has_pending: false,
          has_cleanup: false,
          can_stop: false,
        },
      },
    }

    render(<BackgroundTasks conversationId="conv-1" />)

    expect(screen.getByText('Build project')).toBeInTheDocument()
  })

  it('counts only unfinished background tasks in the header badge', () => {
    mocks.state = {
      ...mocks.state,
      backgroundTasks: {
        'conv-1': [
          runningTask(),
          { ...runningTask(), id: 'bgt-2', state: 'waiting_input' },
          { ...runningTask(), id: 'bgt-3', state: 'succeeded' },
        ],
      },
    }

    render(<BackgroundTasksButton conversationId="conv-1" />)

    const button = screen.getByRole('button', { name: '后台任务（2 项进行中）' })
    expect(button).toHaveTextContent('2')
    fireEvent.click(button)
    expect(mocks.openBackgroundTasks).toHaveBeenCalledWith('conv-1')
  })

  it('announces selected state and closes the panel on a second click', () => {
    mocks.panel = {
      ...mocks.panel,
      view: { type: 'background-tasks', conversationId: 'conv-1' },
    }

    render(<BackgroundTasksButton conversationId="conv-1" />)

    const button = screen.getByRole('button', { name: '后台任务（1 项进行中）' })
    expect(button).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(button)
    expect(mocks.closePanel).toHaveBeenCalledOnce()
  })

  it('does not let an older generation hide Stop all for newly admitted work', () => {
    mocks.state = {
      ...mocks.state,
      backgroundTasks: {
        'conv-1': [runningTask(), { ...runningTask(), id: 'bgt-new', execution_generation: 5 }],
      },
      stopAllStatus: {
        'conv-1': {
          execution_generation: 4,
          requested_at: '2026-09-22T00:00:00+00:00',
          cleanup_pending: true,
        },
      },
    }

    render(<BackgroundTasks conversationId="conv-1" />)

    expect(screen.getByRole('button', { name: '全部停止' })).toBeInTheDocument()
    expect(screen.queryByText('正在停止全部工作')).not.toBeInTheDocument()
  })

  it('does not force a baseline bootstrap over a stream that starts during polling', async () => {
    render(<BackgroundTasksButton conversationId="conv-1" />)

    await vi.advanceTimersByTimeAsync(30_000)

    expect(mocks.loadMessages).toHaveBeenCalledWith(expect.anything(), 'conv-1', {
      preserveLoadedHistory: true,
      preserveOtherConversationStream: true,
      throwOnError: true,
    })
  })

  it('keeps checking for new tasks while a foreground response is streaming', async () => {
    mocks.state = {
      ...mocks.state,
      backgroundTasks: { 'conv-1': [] },
      streamingConversationId: 'conv-1',
    }

    render(<BackgroundTasksButton conversationId="conv-1" />)
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(5_000)

    expect(mocks.refreshBackground).toHaveBeenCalledTimes(2)
  })

  it('refreshes control status immediately while Stop-all cleanup is pending', async () => {
    mocks.state = {
      ...mocks.state,
      backgroundTasks: { 'conv-1': [] },
      backgroundSummary: {
        'conv-1': {
          has_inflight: false,
          has_pending: false,
          has_cleanup: false,
          can_stop: false,
        },
      },
      stopAllStatus: {
        'conv-1': {
          execution_generation: 4,
          requested_at: '2026-09-22T00:00:00+00:00',
          cleanup_pending: true,
        },
      },
    }

    render(<BackgroundTasksButton conversationId="conv-1" />)
    await vi.advanceTimersByTimeAsync(0)

    expect(mocks.loadMessages).toHaveBeenCalledWith(expect.anything(), 'conv-1', {
      preserveLoadedHistory: true,
      preserveOtherConversationStream: true,
      throwOnError: true,
    })
  })

  it('refreshes control status immediately while foreground Stop cleanup is pending', async () => {
    mocks.state = {
      ...mocks.state,
      backgroundTasks: { 'conv-1': [] },
      backgroundSummary: {
        'conv-1': {
          has_inflight: false,
          has_pending: false,
          has_cleanup: false,
          can_stop: false,
        },
      },
      runControl: {
        'conv-1': {
          run_id: 'run-stopping',
          stop_requested_at: '2026-09-22T00:00:00+00:00',
          cleanup_pending: true,
          can_stop: false,
        },
      },
    }

    render(<BackgroundTasksButton conversationId="conv-1" />)
    await vi.advanceTimersByTimeAsync(0)

    expect(mocks.loadMessages).toHaveBeenCalledWith(expect.anything(), 'conv-1', {
      preserveLoadedHistory: true,
      preserveOtherConversationStream: true,
      throwOnError: true,
    })
  })

  it('keeps visibility refreshes in the existing polling loop', async () => {
    let resolveRefresh: (() => void) | null = null
    mocks.refreshBackground.mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          resolveRefresh = resolve
        }),
    )
    render(<BackgroundTasksButton conversationId="conv-1" />)
    await vi.advanceTimersByTimeAsync(0)

    document.dispatchEvent(new Event('visibilitychange'))

    expect(mocks.refreshBackground).toHaveBeenCalledTimes(1)
    resolveRefresh?.()
    await vi.advanceTimersByTimeAsync(5_000)
    expect(mocks.refreshBackground).toHaveBeenCalledTimes(2)
  })
})
