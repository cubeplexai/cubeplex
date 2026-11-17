import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { usePanelStore } from '@cubeplex/core'

const mockFilesMount = vi.hoisted(() => vi.fn())
const mockTerminalMount = vi.hoisted(() => vi.fn())

vi.mock('@/components/panel/sandbox/SandboxFilesView', () => ({
  SandboxFilesView: () => {
    mockFilesMount()
    return <div data-testid="mock-files-view" />
  },
}))
vi.mock('@/components/panel/sandbox/SandboxTerminalView', () => ({
  SandboxTerminalView: () => {
    mockTerminalMount()
    return <div data-testid="mock-terminal-view" />
  },
}))
vi.mock('@/components/panel/BrowserView', () => ({
  BrowserView: () => <div data-testid="mock-browser-view" />,
}))

import { SandboxPanel } from '@/components/panel/sandbox/SandboxPanel'

describe('SandboxPanel', () => {
  beforeEach(() => {
    mockFilesMount.mockReset()
    mockTerminalMount.mockReset()
    usePanelStore.getState().close()
  })

  it('mounts the requested initialTab directly without mounting files first', () => {
    usePanelStore.getState().openSandbox('terminal')
    render(<SandboxPanel workspaceId="ws-1" />)

    expect(screen.getByTestId('mock-terminal-view')).toBeInTheDocument()
    expect(mockTerminalMount).toHaveBeenCalledTimes(1)
    expect(mockFilesMount).not.toHaveBeenCalled()
  })

  it('prefers files when initialFilePath is set even if initialTab was terminal', () => {
    usePanelStore.setState({
      view: {
        type: 'sandbox',
        initialTab: 'terminal',
        initialFilePath: '/workspace/README.md',
        revision: 1,
      },
    })
    render(<SandboxPanel workspaceId="ws-1" />)

    expect(screen.getByTestId('mock-files-view')).toBeInTheDocument()
    expect(mockFilesMount).toHaveBeenCalledTimes(1)
    expect(mockTerminalMount).not.toHaveBeenCalled()
  })
})
