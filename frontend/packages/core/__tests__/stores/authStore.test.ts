import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { useAuthStore } from '../../src/stores/authStore'
import { createApiClient } from '../../src/api/client'

describe('authStore', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  beforeEach(() => {
    useAuthStore.setState({ user: null, isLoading: false, error: null })
    fetchMock = vi.fn()
    globalThis.fetch = fetchMock as unknown as typeof fetch
  })
  afterEach(() => vi.restoreAllMocks())

  it('loadMe populates user on 200', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ id: 'u1', email: 'a@b.c' }), { status: 200 }),
    )
    const client = createApiClient('')
    await useAuthStore.getState().loadMe(client)
    expect(useAuthStore.getState().user).toEqual({ id: 'u1', email: 'a@b.c' })
  })

  it('loadMe leaves user null on 401', async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 401 }))
    const client = createApiClient('')
    await useAuthStore.getState().loadMe(client)
    expect(useAuthStore.getState().user).toBeNull()
  })

  it('reset clears user', () => {
    useAuthStore.setState({ user: { id: 'u1', email: 'a@b.c' } })
    useAuthStore.getState().reset()
    expect(useAuthStore.getState().user).toBeNull()
  })

  it('a slower stale loadMe response does not clobber a newer one', async () => {
    // Two concurrent loadMe calls (e.g. one from an onboarding page mount
    // effect, one from the app shell it navigates into) can resolve out of
    // order. The response to the *older* request must be discarded even if
    // it lands last.
    let resolveFirst!: (res: Response) => void
    fetchMock
      .mockImplementationOnce(() => new Promise((resolve) => (resolveFirst = resolve)))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ id: 'fresh', email: 'fresh@b.c' }), { status: 200 }),
      )

    const client = createApiClient('')
    const firstCall = useAuthStore.getState().loadMe(client)
    await useAuthStore.getState().loadMe(client)
    expect(useAuthStore.getState().user).toEqual({ id: 'fresh', email: 'fresh@b.c' })

    resolveFirst(new Response(JSON.stringify({ id: 'stale', email: 'stale@b.c' }), { status: 200 }))
    await firstCall
    expect(useAuthStore.getState().user).toEqual({ id: 'fresh', email: 'fresh@b.c' })
  })
})
