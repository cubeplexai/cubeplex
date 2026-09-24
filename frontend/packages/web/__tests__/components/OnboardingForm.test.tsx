import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  completeOnboarding: vi.fn(),
  loadMe: vi.fn(),
  replace: vi.fn(),
  client: {},
}))

vi.mock('@cubeplex/core', () => {
  const useAuthStore = Object.assign(
    (selector: (state: Record<string, unknown>) => unknown) =>
      selector({ user: { email: 'new@example.com', org_memberships: [], needs_onboarding: true } }),
    { getState: () => ({ loadMe: mocks.loadMe }) },
  )
  return {
    completeOnboarding: mocks.completeOnboarding,
    createApiClient: () => mocks.client,
    useAuthStore,
  }
})

vi.mock('next/navigation', () => ({ useRouter: () => ({ replace: mocks.replace }) }))
vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }))

import { OnboardingForm } from '@/components/onboarding/OnboardingForm'

describe('OnboardingForm', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('refreshes the onboarding gate before navigating to the new workspace', async () => {
    let finishLoadMe!: () => void
    mocks.completeOnboarding.mockResolvedValue({ workspace_id: 'ws-new' })
    mocks.loadMe.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finishLoadMe = resolve
        }),
    )

    render(<OnboardingForm />)
    fireEvent.change(screen.getByLabelText('orgName'), { target: { value: 'Example Org' } })
    fireEvent.change(screen.getByLabelText('orgSlug'), { target: { value: 'example-org' } })
    fireEvent.change(screen.getByLabelText('workspaceName'), {
      target: { value: 'Personal' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'createOrgAndWorkspace' }))

    await waitFor(() => expect(mocks.completeOnboarding).toHaveBeenCalledOnce())
    expect(mocks.replace).not.toHaveBeenCalled()

    await waitFor(() => expect(mocks.loadMe).toHaveBeenCalledWith(mocks.client))
    await act(async () => finishLoadMe())
    expect(mocks.replace).toHaveBeenCalledWith('/w/ws-new')
  })
})
