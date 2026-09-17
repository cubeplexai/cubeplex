import type { StreamConnection } from '@cubeplex/core'

export type RunChipStatus =
  'completed' | 'stopping' | 'stopped' | 'reconnecting' | 'disconnected' | 'failed' | 'incomplete'

/**
 * Run-level chip (failed / reconnecting / stopping) belongs on the live
 * streaming bubble, or on the last assistant of that run in history.
 * Intermediate tool-use bubbles of a multi-step run share ``run_id`` but
 * are not the run-status surface — otherwise a refresh paints "Reply failed"
 * on every turn of the failed run.
 */
export function isRunStatusSurface(opts: { isLive: boolean; isRunAnchor: boolean }): boolean {
  return opts.isLive || opts.isRunAnchor
}

export function conversationErrorApplies(
  error: { runId: string } | null,
  opts: {
    messageRunId?: string | null
    isLive: boolean
    isRunAnchor: boolean
  },
): boolean {
  if (!error) return false
  if (!isRunStatusSurface(opts)) return false
  if (opts.messageRunId == null || opts.messageRunId === '') return true
  if (!error.runId) return true
  return error.runId === opts.messageRunId
}

export function isLiveRunChip(opts: {
  isLiveStreaming: boolean
  isRunAnchor: boolean
  messageRunId?: string | null
  currentRunId: string | null
  streamConnection: StreamConnection
}): boolean {
  if (opts.isLiveStreaming) return true
  if (!opts.isRunAnchor) return false
  return (
    opts.messageRunId != null &&
    opts.messageRunId === opts.currentRunId &&
    opts.streamConnection != null &&
    opts.streamConnection !== 'connected'
  )
}

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
