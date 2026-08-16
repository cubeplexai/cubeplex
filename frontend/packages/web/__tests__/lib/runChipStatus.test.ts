import { describe, expect, it } from 'vitest'
import { deriveRunChipStatus } from '@/lib/runChipStatus'

describe('deriveRunChipStatus', () => {
  it('keeps a live connected run silent', () => {
    expect(
      deriveRunChipStatus({
        isLive: true,
        cancelling: false,
        streamConnection: 'connected',
        hasError: false,
        isStaleLastRun: false,
      }),
    ).toBe('completed')
  })

  it('shows stopping / reconnect over history stop_reason', () => {
    expect(
      deriveRunChipStatus({
        isLive: true,
        cancelling: true,
        streamConnection: 'reconnecting',
        stopReason: 'error',
        hasError: true,
        isStaleLastRun: false,
      }),
    ).toBe('stopping')
    expect(
      deriveRunChipStatus({
        isLive: true,
        cancelling: false,
        streamConnection: 'reconnecting',
        hasError: false,
        isStaleLastRun: false,
      }),
    ).toBe('reconnecting')
  })

  it('maps aborted and error history turns', () => {
    expect(
      deriveRunChipStatus({
        isLive: false,
        cancelling: false,
        streamConnection: null,
        stopReason: 'aborted',
        hasError: false,
        isStaleLastRun: false,
      }),
    ).toBe('stopped')
    expect(
      deriveRunChipStatus({
        isLive: false,
        cancelling: false,
        streamConnection: null,
        stopReason: 'error',
        hasError: false,
        isStaleLastRun: false,
      }),
    ).toBe('failed')
  })
})
