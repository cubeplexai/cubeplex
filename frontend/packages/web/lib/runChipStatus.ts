import type { StreamConnection } from '@cubeplex/core'

export type RunChipStatus =
  'completed' | 'stopping' | 'stopped' | 'reconnecting' | 'disconnected' | 'failed' | 'incomplete'

export function deriveRunChipStatus(input: {
  isLive: boolean
  cancelling: boolean
  streamConnection: StreamConnection
  stopReason?: string | null
  hasError: boolean
  isStaleLastRun: boolean
}): RunChipStatus {
  if (input.isLive) {
    if (input.cancelling) return 'stopping'
    if (input.streamConnection === 'reconnecting') return 'reconnecting'
    if (input.streamConnection === 'disconnected') return 'disconnected'
    return 'completed'
  }
  if (input.stopReason === 'aborted') return 'stopped'
  if (input.stopReason === 'error' || input.hasError) return 'failed'
  if (input.isStaleLastRun) return 'incomplete'
  return 'completed'
}
