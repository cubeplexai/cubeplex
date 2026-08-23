import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/hooks/useSandboxTerminal', () => ({
  useSandboxTerminal: () => ({
    url: 'https://terminal.example/',
    loading: false,
    error: undefined,
    refresh: vi.fn(),
  }),
}))

import { SandboxTerminalView } from '@/components/panel/sandbox/SandboxTerminalView'

describe('SandboxTerminalView', () => {
  it('keeps the terminal loading surface visible until the iframe loads', () => {
    render(<SandboxTerminalView workspaceId="ws-1" />)

    const iframe = screen.getByTitle('Sandbox terminal')
    expect(screen.getByText('Starting terminal…')).toBeInTheDocument()
    expect(iframe).toHaveClass('opacity-0')

    fireEvent.load(iframe)

    expect(screen.queryByText('Starting terminal…')).not.toBeInTheDocument()
    expect(iframe).not.toHaveClass('opacity-0')
  })
})
