import { act, renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { SWRConfig } from 'swr'

import { shouldRetryTerminalError, useSandboxTerminal } from '@/hooks/useSandboxTerminal'

describe('shouldRetryTerminalError', () => {
  it('retries transient 5xx, 408/429, and network errors', () => {
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 503'))).toBe(true)
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 502'))).toBe(true)
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 504'))).toBe(true)
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 500'))).toBe(true)
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 408'))).toBe(true)
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 429'))).toBe(true)
    expect(shouldRetryTerminalError(new TypeError('Failed to fetch'))).toBe(true)
  })

  it('skips deterministic 4xx and 501 errors', () => {
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 400'))).toBe(false)
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 401'))).toBe(false)
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 403'))).toBe(false)
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 404'))).toBe(false)
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 422'))).toBe(false)
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 501'))).toBe(false)
  })
})

describe('useSandboxTerminal', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(SWRConfig, { value: { provider: () => new Map() } }, children)

  it('keeps loading true without flashing error during retry backoff', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('starting', { status: 503 }))
      .mockResolvedValueOnce(Response.json({ url: 'https://panel.example/sandbox-panel/tok/' }))
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useSandboxTerminal('ws-1', true), { wrapper })

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    // During the 3s backoff after a 503, loading stays true and error is hidden.
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(result.current.loading).toBe(true)
    expect(result.current.error).toBeUndefined()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
      await Promise.resolve()
    })

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toBeUndefined()
    expect(result.current.url).toBe('https://panel.example/sandbox-panel/tok/')
  })

  it('cancels pending retry timer when manual refresh succeeds during backoff', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('starting', { status: 503 }))
      .mockResolvedValueOnce(Response.json({ url: 'https://panel.example/sandbox-panel/manual/' }))
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useSandboxTerminal('ws-1', true), { wrapper })

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(result.current.loading).toBe(true)

    await act(async () => {
      await result.current.refresh()
    })

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(result.current.loading).toBe(false)
    expect(result.current.url).toBe('https://panel.example/sandbox-panel/manual/')

    // Advancing past the old 3s backoff must not fire a stale revalidation.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
      await Promise.resolve()
    })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('surfaces error immediately on non-retriable 403/501', async () => {
    const fetch403 = vi.fn().mockResolvedValueOnce(new Response('forbidden', { status: 403 }))
    vi.stubGlobal('fetch', fetch403)

    const { result } = renderHook(() => useSandboxTerminal('ws-1', true), { wrapper })
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(fetch403).toHaveBeenCalledTimes(1)
    expect(result.current.loading).toBe(false)
    expect(result.current.error?.message).toBe('terminal fetch failed: 403')
  })

  it('surfaces error after retry exhaustion', async () => {
    const fetch503 = vi.fn().mockImplementation(async () => new Response('down', { status: 503 }))
    vi.stubGlobal('fetch', fetch503)

    const { result } = renderHook(() => useSandboxTerminal('ws-2', true), { wrapper })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    for (let i = 0; i < 4; i++) {
      expect(result.current.loading).toBe(true)
      expect(result.current.error).toBeUndefined()
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000)
      })
    }

    expect(fetch503).toHaveBeenCalledTimes(5)
    expect(result.current.loading).toBe(false)
    expect(result.current.error?.message).toBe('terminal fetch failed: 503')
  })
})
