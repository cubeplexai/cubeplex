import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ToolCallGroup } from '../ToolCallGroup'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

vi.mock('@cubeplex/core', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@cubeplex/core')>()
  return {
    ...actual,
    useMcpToolRegistryStore: (selector: (s: { lookup: (name: string) => null }) => unknown) =>
      selector({ lookup: () => null }),
    useToolDetailStore: (selector: (s: { open: () => void }) => unknown) =>
      selector({ open: vi.fn() }),
  }
})

const executeBlock = {
  type: 'tool_call' as const,
  id: 'tc-1',
  name: 'execute',
  arguments: { command: 'ls', description: 'List files' },
}

describe('ToolCallGroup running execute chip', () => {
  it('keeps a spinner when the result is still running', () => {
    const { container } = render(
      <ToolCallGroup
        blocks={[executeBlock]}
        toolResultMap={{
          'tc-1': {
            content: 'partial',
            receivedAt: Date.now(),
            details: { status: 'running' },
          },
        }}
        isStreaming
      />,
    )
    expect(container.querySelector('.animate-spin')).not.toBeNull()
    expect(container.querySelector('.text-success-fg')).toBeNull()
  })

  it('shows a check when the command has exited', () => {
    const { container } = render(
      <ToolCallGroup
        blocks={[executeBlock]}
        toolResultMap={{
          'tc-1': {
            content: 'done',
            receivedAt: Date.now(),
            details: { status: 'exited' },
          },
        }}
        isStreaming={false}
      />,
    )
    expect(container.querySelector('.animate-spin')).toBeNull()
    expect(container.querySelector('.text-success-fg')).not.toBeNull()
    expect(screen.getByText(/execute/i)).toBeInTheDocument()
  })
})
