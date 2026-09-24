import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore, type MeResult } from '@cubeplex/core'

const pendingUser: MeResult = {
  id: 'user-1',
  email: 'new@example.com',
  display_name: null,
  avatar_url: null,
  avatar_seed: null,
  avatar_kind: null,
  avatar_style: null,
  language: 'en',
  is_verified: true,
  org_memberships: [],
  needs_onboarding: true,
}

const mocks = vi.hoisted(() => ({
  completeOnboarding: vi.fn(),
  loadMe: vi.fn(),
  replace: vi.fn(),
  client: {},
}))

vi.mock('@cubeplex/core', async () => {
  const { create } = await import('zustand')
  const useAuthStore = create(() => ({
    user: null as MeResult | null,
    error: null as string | null,
    loadMe: mocks.loadMe,
  }))
  return {
    completeOnboarding: mocks.completeOnboarding,
    createApiClient: () => mocks.client,
    useAuthStore,
  }
})

vi.mock('next/navigation', () => ({ useRouter: () => ({ replace: mocks.replace }) }))
vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }))

import { OnboardingForm } from '@/components/onboarding/OnboardingForm'
import OnboardingPage from '@/app/(setup)/onboarding/page'

describe('OnboardingForm', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAuthStore.setState({ user: pendingUser, error: null })
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
    await act(async () => {
      useAuthStore.setState({ user: { ...pendingUser, needs_onboarding: false } })
      finishLoadMe()
    })
    expect(mocks.replace).toHaveBeenCalledWith('/w/ws-new')
  })

  it('does not race the workspace navigation with the onboarding-page redirect', async () => {
    mocks.completeOnboarding.mockResolvedValue({ workspace_id: 'ws-new' })
    mocks.loadMe.mockResolvedValueOnce(undefined)
    mocks.loadMe.mockImplementationOnce(async () => {
      useAuthStore.setState({ user: { ...pendingUser, needs_onboarding: false } })
    })

    render(<OnboardingPage />)
    fireEvent.change(screen.getByLabelText('orgName'), { target: { value: 'Example Org' } })
    fireEvent.change(screen.getByLabelText('orgSlug'), { target: { value: 'example-org' } })
    fireEvent.change(screen.getByLabelText('workspaceName'), {
      target: { value: 'Personal' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'createOrgAndWorkspace' }))

    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith('/w/ws-new'))
    expect(mocks.replace).not.toHaveBeenCalledWith('/')
  })

  it('redirects an already-onboarded visitor away from onboarding', async () => {
    useAuthStore.setState({ user: { ...pendingUser, needs_onboarding: false } })
    mocks.loadMe.mockResolvedValue(undefined)

    render(<OnboardingPage />)

    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith('/'))
    expect(mocks.replace).not.toHaveBeenCalledWith('/w/ws-new')
  })

  it('keeps the user on onboarding when account refresh fails', async () => {
    mocks.completeOnboarding.mockResolvedValue({ workspace_id: 'ws-new' })
    mocks.loadMe.mockResolvedValueOnce(undefined)
    mocks.loadMe.mockImplementationOnce(async () => {
      useAuthStore.setState({ error: 'Unable to load account' })
    })

    render(<OnboardingPage />)
    fireEvent.change(screen.getByLabelText('orgName'), { target: { value: 'Example Org' } })
    fireEvent.change(screen.getByLabelText('orgSlug'), { target: { value: 'example-org' } })
    fireEvent.change(screen.getByLabelText('workspaceName'), {
      target: { value: 'Personal' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'createOrgAndWorkspace' }))

    await waitFor(() => expect(screen.getByText('Unable to load account')).toBeVisible())
    expect(mocks.replace).not.toHaveBeenCalled()
  })
})
