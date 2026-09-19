import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockUseSandboxTerminal = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/useSandboxTerminal', () => ({
  useSandboxTerminal: mockUseSandboxTerminal,
}))

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
    vi.mocked(fetch).mockReset()
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => [] } as Response)
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

  it('renders a Kill button for a running sandbox command', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => [
        {
          id: 'scmd-1',
          description: 'dev server',
          status: 'running',
          started_at: '2026-09-18T00:00:00+00:00',
          kind: 'execute',
          lifetime: 'conversation',
        },
      ],
    } as Response)
    render(<SandboxTerminalView workspaceId="ws-1" conversationId="conv-1" />)
    expect(await screen.findByText('dev server')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Kill dev server' })).toBeInTheDocument()
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
      json: async () => [
        {
          id: 'scmd-1',
          description: 'dev server',
          status: 'running',
          started_at: '2026-09-18T00:00:00+00:00',
          kind: 'execute',
          lifetime: 'conversation',
        },
      ],
    } as Response)

    render(<SandboxTerminalView workspaceId="ws-1" conversationId="conv-1" />)

    expect(await screen.findByRole('button', { name: 'Kill dev server' })).toBeInTheDocument()
    expect(screen.getByText(/Could not start terminal/)).toBeInTheDocument()
  })

  it('kills a running command and refreshes the durable list', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          {
            id: 'scmd-1',
            description: 'dev server',
            status: 'running',
            started_at: new Date().toISOString(),
            kind: 'execute',
            lifetime: 'conversation',
          },
        ],
      } as Response)
      .mockResolvedValueOnce({ ok: true } as Response)
      .mockResolvedValueOnce({ ok: true, json: async () => [] } as Response)

    render(<SandboxTerminalView workspaceId="ws-1" conversationId="conv-1" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Kill dev server' }))

    await vi.waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        '/api/v1/ws/ws-1/conversations/conv-1/sandbox-commands/scmd-1/kill',
        expect.objectContaining({ method: 'POST' }),
      )
    })
  })
})
