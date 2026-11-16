import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  push: vi.fn(),
  client: { baseUrl: '' },
}))

vi.mock('@cubeplex/core', () => ({
  createApiClient: () => mocks.client,
  useWorkspaceStore: {
    getState: () => ({ create: mocks.create }),
  },
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mocks.push }),
}))

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

import { WorkspaceCreateForm } from '@/components/workspace/WorkspaceCreateForm'

describe('WorkspaceCreateForm', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('defaults sandboxMode to per_user when creating a workspace', async () => {
    mocks.create.mockResolvedValue({ id: 'ws-1', name: 'Personal', sandbox_mode: 'per_user' })

    render(<WorkspaceCreateForm />)
    fireEvent.change(screen.getByPlaceholderText('namePlaceholder'), {
      target: { value: 'Personal' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'submit' }))

    await waitFor(() =>
      expect(mocks.create).toHaveBeenCalledWith(mocks.client, 'Personal', 'per_user'),
    )
    expect(mocks.push).toHaveBeenCalledWith('/w/ws-1')
  })

  it('passes sandboxMode=workspace when workspace shared sandbox is chosen', async () => {
    mocks.create.mockResolvedValue({ id: 'ws-2', name: 'Shared Eng', sandbox_mode: 'workspace' })

    render(<WorkspaceCreateForm />)
    fireEvent.change(screen.getByPlaceholderText('namePlaceholder'), {
      target: { value: 'Shared Eng' },
    })
    const radios = screen.getAllByRole('radio')
    const sharedRadio = radios.find((r) => (r as HTMLInputElement).value === 'workspace')!
    fireEvent.click(sharedRadio)
    fireEvent.click(screen.getByRole('button', { name: 'submit' }))

    await waitFor(() =>
      expect(mocks.create).toHaveBeenCalledWith(mocks.client, 'Shared Eng', 'workspace'),
    )
    expect(mocks.push).toHaveBeenCalledWith('/w/ws-2')
  })
})
