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
      new Response(JSON.stringify({ id: 'u1', email: 'a@b.c' }), { status: 200 })
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
})
