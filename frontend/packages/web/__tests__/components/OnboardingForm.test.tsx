import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApiClient, useAuthStore, type MeResult } from '@cubeplex/core'

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
  push: vi.fn(),
  reset: vi.fn(),
  clearStream: vi.fn(),
  resetUnread: vi.fn(),
  unauthorized: null as (() => void) | null,
  client: { baseUrl: '', onUnauthorized: vi.fn() },
}))

vi.mock('@cubeplex/core', async () => {
  const { create } = await import('zustand')
  const useAuthStore = create(() => ({
    user: null as MeResult | null,
    error: null as string | null,
    loadMe: mocks.loadMe,
    reset: mocks.reset,
  }))
  return {
    completeOnboarding: mocks.completeOnboarding,
    createApiClient: () => mocks.client,
    useAuthStore,
    useMessageStore: {
      getState: () => ({ clearStream: mocks.clearStream, resetUnread: mocks.resetUnread }),
    },
  }
})

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: mocks.replace, push: mocks.push }),
  usePathname: () => '/onboarding',
}))
vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }))

import { OnboardingForm } from '@/components/onboarding/OnboardingForm'
import OnboardingPage from '@/app/(setup)/onboarding/page'

describe('OnboardingForm', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.unauthorized = null
    mocks.client.onUnauthorized.mockImplementation((handler: () => void) => {
      mocks.unauthorized = handler
      return () => {
        if (mocks.unauthorized === handler) mocks.unauthorized = null
      }
    })
    mocks.reset.mockImplementation(() => useAuthStore.setState({ user: null }))
    useAuthStore.setState({ user: pendingUser, error: null })
  })

  afterEach(() => vi.unstubAllGlobals())

  it('refreshes the onboarding gate before navigating to the new workspace', async () => {
    let finishLoadMe!: () => void
    mocks.completeOnboarding.mockResolvedValue({ workspace_id: 'ws-new' })
    mocks.loadMe.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finishLoadMe = resolve
        }),
    )

    render(<OnboardingForm client={createApiClient('')} />)
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

  it('suppresses the page redirect while an onboarding POST is in flight', async () => {
    let finishInitialLoad!: () => void
    let finishPost!: (result: { workspace_id: string }) => void
    mocks.loadMe.mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          finishInitialLoad = resolve
        }),
    )
    mocks.loadMe.mockImplementationOnce(async () => {
      useAuthStore.setState({ user: { ...pendingUser, needs_onboarding: false } })
    })
    mocks.completeOnboarding.mockImplementation(
      () =>
        new Promise<{ workspace_id: string }>((resolve) => {
          finishPost = resolve
        }),
    )

    render(<OnboardingPage />)
    fireEvent.change(screen.getByLabelText('orgName'), { target: { value: 'Example Org' } })
    fireEvent.change(screen.getByLabelText('orgSlug'), { target: { value: 'example-org' } })
    fireEvent.change(screen.getByLabelText('workspaceName'), {
      target: { value: 'Personal' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'createOrgAndWorkspace' }))
    await waitFor(() => expect(mocks.completeOnboarding).toHaveBeenCalledOnce())

    await act(async () => {
      useAuthStore.setState({ user: { ...pendingUser, needs_onboarding: false } })
      finishInitialLoad()
    })
    expect(mocks.replace).not.toHaveBeenCalledWith('/')

    await act(async () => finishPost({ workspace_id: 'ws-new' }))
    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith('/w/ws-new'))
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

  it('sends an expired session to login after the completed POST', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true }))
    mocks.completeOnboarding.mockResolvedValue({ workspace_id: 'ws-new' })
    mocks.loadMe.mockResolvedValueOnce(undefined)
    mocks.loadMe.mockImplementationOnce(async () => {
      useAuthStore.setState({ user: null })
      mocks.unauthorized?.()
    })

    render(<OnboardingPage />)
    fireEvent.change(screen.getByLabelText('orgName'), { target: { value: 'Example Org' } })
    fireEvent.change(screen.getByLabelText('orgSlug'), { target: { value: 'example-org' } })
    fireEvent.change(screen.getByLabelText('workspaceName'), {
      target: { value: 'Personal' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'createOrgAndWorkspace' }))

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/login?next=%2Fonboarding'))
    expect(mocks.replace).not.toHaveBeenCalledWith('/w/ws-new')
  })
})
