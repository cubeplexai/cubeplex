import { describe, expect, it } from 'vitest'
import { conversationErrorApplies, deriveRunChipStatus, isLiveRunChip } from '@/lib/runChipStatus'

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

  it('does not paint a conversation error on intermediate turns of the same run', () => {
    const error = { runId: 'run-1' }
    expect(
      conversationErrorApplies(error, {
        messageRunId: 'run-1',
        isLive: false,
        isRunAnchor: false,
      }),
    ).toBe(false)
    expect(
      conversationErrorApplies(error, {
        messageRunId: 'run-1',
        isLive: false,
        isRunAnchor: true,
      }),
    ).toBe(true)
    expect(
      conversationErrorApplies(error, {
        messageRunId: 'run-1',
        isLive: true,
        isRunAnchor: false,
      }),
    ).toBe(true)
    expect(
      conversationErrorApplies(error, {
        messageRunId: 'run-other',
        isLive: false,
        isRunAnchor: true,
      }),
    ).toBe(false)
    expect(conversationErrorApplies(null, { isLive: false, isRunAnchor: true })).toBe(false)
  })

  it('keeps reconnect/disconnect on the run-anchor bubble only', () => {
    expect(
      isLiveRunChip({
        isLiveStreaming: true,
        isRunAnchor: false,
        messageRunId: 'run-1',
        currentRunId: 'run-1',
        streamConnection: 'reconnecting',
      }),
    ).toBe(true)
    expect(
      isLiveRunChip({
        isLiveStreaming: false,
        isRunAnchor: false,
        messageRunId: 'run-1',
        currentRunId: 'run-1',
        streamConnection: 'reconnecting',
      }),
    ).toBe(false)
    expect(
      isLiveRunChip({
        isLiveStreaming: false,
        isRunAnchor: true,
        messageRunId: 'run-1',
        currentRunId: 'run-1',
        streamConnection: 'reconnecting',
      }),
    ).toBe(true)
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
