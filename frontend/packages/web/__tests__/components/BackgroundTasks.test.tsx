import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  stopTask: vi.fn(),
  stopAllWork: vi.fn(),
  refreshBackground: vi.fn(),
  loadMessages: vi.fn(),
  setWorkspaceId: vi.fn(),
  state: {} as Record<string, unknown>,
}))

vi.mock('@cubeplex/core', () => {
  const useMessageStore = (selector: (state: Record<string, unknown>) => unknown) =>
    selector(mocks.state)
  useMessageStore.getState = () => mocks.state
  return {
    createApiClient: () => ({ setWorkspaceId: mocks.setWorkspaceId }),
    useMessageStore,
  }
})

vi.mock('@/hooks/useWorkspaceContext', () => ({
  useWorkspaceContext: () => ({ workspaceId: 'ws-1' }),
}))

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) =>
    ({
      title: '后台任务',
      description: '独立继续执行',
      stopTask: '停止任务',
      stopAll: '全部停止',
      submittingStop: '正在提交…',
      stopping: '正在停止',
      stoppingAll: '正在停止全部工作',
      finalizing: '正在整理结果',
      refreshFailed: '刷新失败',
      unnamed: '后台任务',
      'states.running': '运行中',
    })[key] ?? key,
}))

import { BackgroundTasks } from '@/components/layout/BackgroundTasks'

function runningTask() {
  return {
    id: 'bgt-1',
    description: 'Build project',
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
    mocks.stopTask.mockResolvedValue(undefined)
    mocks.stopAllWork.mockResolvedValue(undefined)
    mocks.refreshBackground.mockResolvedValue(undefined)
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
      refreshBackground: mocks.refreshBackground,
      loadMessages: mocks.loadMessages,
      stopTask: mocks.stopTask,
      stopAllWork: mocks.stopAllWork,
      streamingConversationId: null,
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
})
