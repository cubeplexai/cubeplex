import { shouldRetryTerminalError } from '@/hooks/useSandboxTerminal'

describe('shouldRetryTerminalError', () => {
  it('retries transient 5xx and network errors', () => {
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 503'))).toBe(true)
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 502'))).toBe(true)
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 504'))).toBe(true)
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 500'))).toBe(true)
    expect(shouldRetryTerminalError(new TypeError('Failed to fetch'))).toBe(true)
  })

  it('skips terminal 401/403/404/501 errors', () => {
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 401'))).toBe(false)
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 403'))).toBe(false)
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 404'))).toBe(false)
    expect(shouldRetryTerminalError(new Error('terminal fetch failed: 501'))).toBe(false)
  })
})
