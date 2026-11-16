import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { MySandboxOut } from '@cubeplex/core'

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(),
  restartMySandbox: vi.fn(),
  deleteMySandbox: vi.fn(),
}))

vi.mock('@/hooks/useMySandboxes', () => ({
  useMySandboxes: () => ({ mutate: mocks.mutate }),
  restartMySandbox: mocks.restartMySandbox,
  deleteMySandbox: mocks.deleteMySandbox,
}))

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

import { SandboxCard } from '@/components/workspace-settings/sandboxes/SandboxCard'

const baseSandbox: MySandboxOut = {
  id: 'sbx-1',
  scope_type: 'workspace',
  scope_id: 'ws-1',
  scope_title: null,
  status: 'running',
  cleanup_action: null,
  cleanup_requested_at: null,
  image: 'cubeplex/sandbox:latest',
  last_activity_at: null,
  created_at: '2026-11-16T00:00:00Z',
}

describe('SandboxCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.mutate.mockResolvedValue(undefined)
  })

  it('hides Restart and Delete buttons and shows adminOnlyHint when can_manage is false', () => {
    render(
      <ul>
        <SandboxCard
          sandbox={{ ...baseSandbox, can_manage: false }}
          wsId="ws-1"
          onMutated={vi.fn()}
        />
      </ul>,
    )

    expect(screen.getByText('scope.workspace')).toBeInTheDocument()
    expect(screen.getByText('adminOnlyHint')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'restart' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'delete' })).not.toBeInTheDocument()
  })

  it('allows workspace admins (can_manage=true) to restart a workspace-shared sandbox', async () => {
    mocks.restartMySandbox.mockResolvedValue(undefined)
    const onMutated = vi.fn()

    render(
      <ul>
        <SandboxCard
          sandbox={{ ...baseSandbox, can_manage: true }}
          wsId="ws-1"
          onMutated={onMutated}
        />
      </ul>,
    )

    expect(screen.getByText('scope.workspace')).toBeInTheDocument()
    expect(screen.queryByText('adminOnlyHint')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'delete' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'restart' }))
    fireEvent.click(await screen.findByRole('button', { name: 'restartConfirm' }))

    await waitFor(() => expect(mocks.restartMySandbox).toHaveBeenCalledWith('ws-1', 'sbx-1'))
    expect(onMutated).toHaveBeenCalledOnce()
  })
})
