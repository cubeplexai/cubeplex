import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockUseSandboxTerminal = vi.hoisted(() => vi.fn())
const mockToastError = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/useSandboxTerminal', () => ({
  useSandboxTerminal: mockUseSandboxTerminal,
}))
vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) =>
    key === 'stopFailed' ? 'Could not stop this task. Try again.' : key,
}))
vi.mock('sonner', () => ({ toast: { error: mockToastError } }))

vi.stubGlobal('fetch', vi.fn())

import { SandboxTerminalView } from '@/components/panel/sandbox/SandboxTerminalView'

describe('SandboxTerminalView', () => {
  beforeEach(() => {
    mockUseSandboxTerminal.mockReturnValue({
      url: 'https://terminal.example/',
      loading: false,
      error: undefined,
      refresh: vi.fn(),
    })
    mockToastError.mockReset()
    vi.mocked(fetch).mockReset()
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => ({ items: [] }) } as Response)
  })

  it('keeps the terminal loading surface visible until the iframe loads', () => {
    render(<SandboxTerminalView workspaceId="ws-1" />)

    const iframe = screen.getByTitle('Sandbox terminal')
    expect(screen.getByText('Starting terminal…')).toBeInTheDocument()
    expect(iframe).toHaveClass('opacity-0')

    fireEvent.load(iframe)

    expect(screen.queryByText('Starting terminal…')).not.toBeInTheDocument()
    expect(iframe).not.toHaveClass('opacity-0')
  })

  it('renders a Stop button for a running background command', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            id: 'btask-1',
            description: 'dev server',
            state: 'running',
            created_at: '2026-09-18T00:00:00+00:00',
            kind: 'execute',
            stop_requested_at: null,
            capabilities: { can_stop: true },
          },
        ],
      }),
    } as Response)
    render(<SandboxTerminalView workspaceId="ws-1" conversationId="conv-1" />)
    expect(await screen.findByText('dev server')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stop dev server' })).toBeInTheDocument()
  })

  it('does not offer Stop for a terminal task that is only finalizing logs', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            id: 'btask-1',
            description: 'finished build',
            state: 'succeeded',
            created_at: '2026-09-18T00:00:00+00:00',
            kind: 'execute',
            stop_requested_at: null,
            cleanup_pending: true,
            capabilities: { can_stop: false },
          },
        ],
      }),
    } as Response)

    render(<SandboxTerminalView workspaceId="ws-1" conversationId="conv-1" />)

    expect(await screen.findByText('finished build')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stop finished build' })).not.toBeInTheDocument()
  })

  it('keeps command controls available when terminal startup fails', async () => {
    mockUseSandboxTerminal.mockReturnValue({
      url: undefined,
      loading: false,
      error: new Error('proxy unavailable'),
      refresh: vi.fn(),
    })
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            id: 'btask-1',
            description: 'dev server',
            state: 'running',
            created_at: '2026-09-18T00:00:00+00:00',
            kind: 'execute',
            stop_requested_at: null,
            capabilities: { can_stop: true },
          },
        ],
      }),
    } as Response)

    render(<SandboxTerminalView workspaceId="ws-1" conversationId="conv-1" />)

    expect(await screen.findByRole('button', { name: 'Stop dev server' })).toBeInTheDocument()
    expect(screen.getByText(/Could not start terminal/)).toBeInTheDocument()
  })

  it('stops a running command through the public task API and refreshes', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          items: [
            {
              id: 'btask-1',
              description: 'dev server',
              state: 'running',
              created_at: new Date().toISOString(),
              kind: 'execute',
              stop_requested_at: null,
              capabilities: { can_stop: true },
            },
          ],
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ accepted: true, cleanup_pending: true, task: {} }),
      } as Response)
      .mockResolvedValueOnce({ ok: true, json: async () => ({ items: [] }) } as Response)
      .mockResolvedValueOnce({ ok: true, json: async () => [] } as Response)

    render(<SandboxTerminalView workspaceId="ws-1" conversationId="conv-1" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Stop dev server' }))

    await vi.waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        '/api/v1/ws/ws-1/conversations/conv-1/background-tasks/btask-1/stop',
        expect.objectContaining({ method: 'POST' }),
      )
    })
  })

  it('keeps pre-cutover commands visible and stoppable through the legacy route', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce({ ok: true, json: async () => ({ items: [] }) } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          {
            id: 'legacy-1',
            description: 'legacy server',
            started_at: new Date().toISOString(),
          },
        ],
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ id: 'legacy-1', status: 'killed' }),
      } as Response)
      .mockResolvedValueOnce({ ok: true, json: async () => ({ items: [] }) } as Response)
      .mockResolvedValueOnce({ ok: true, json: async () => [] } as Response)

    render(<SandboxTerminalView workspaceId="ws-1" conversationId="conv-1" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Stop legacy server' }))

    await vi.waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        '/api/v1/ws/ws-1/conversations/conv-1/sandbox-commands/legacy-1/kill',
        expect.objectContaining({ method: 'POST' }),
      )
    })
  })

  it('reports a legacy stop rejection instead of silently ignoring it', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce({ ok: true, json: async () => ({ items: [] }) } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          {
            id: 'legacy-1',
            description: 'legacy server',
            started_at: new Date().toISOString(),
          },
        ],
      } as Response)
      .mockResolvedValueOnce({ ok: false, status: 503 } as Response)

    render(<SandboxTerminalView workspaceId="ws-1" conversationId="conv-1" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Stop legacy server' }))

    await vi.waitFor(() => {
      expect(mockToastError).toHaveBeenCalledWith('Could not stop this task. Try again.')
    })
  })
})
