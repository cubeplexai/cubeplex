'use client'

import { useMemo, useRef } from 'react'
import { getSubagentSummary } from '@cubeplex/core'
import type { Message } from '@cubeplex/core'

type ToolResultEntry = {
  content: string
  receivedAt: number
  startedAt?: number
  contentType?: string
}

/**
 * For each assistant message, the set of tool_call_ids it owns:
 *   - top-level `tool_call` blocks on the message itself
 *   - inner tool_call_ids of any `subagent` block (extracted from the
 *     matching tool_result message's `subagent_events` summary)
 *
 * The inner ids matter because `buildHistoricalToolResultMap` indexes them
 * into the flat `historicalToolResults` map, and `SubAgentCard` later looks
 * them up when the user expands a completed subagent card. Omitting them
 * would render those inner tool results as missing.
 */
function buildOwnedToolCallIdsByMessage(messages: Message[]): Record<string, Set<string>> {
  // First pass: outer subagent tool_call_id → inner ids from the matching
  // tool_result message's subagent_events summary.
  const innerIdsByOuter: Record<string, string[]> = {}
  for (const msg of messages) {
    if (msg.role !== 'tool_result') continue
    if (msg.tool_name !== 'subagent' || !msg.tool_call_id) continue
    const summary = getSubagentSummary(msg)
    if (!summary?.tool_results) continue
    const inner: string[] = []
    for (const tr of summary.tool_results) {
      if (tr.tool_call_id) inner.push(tr.tool_call_id)
    }
    if (inner.length > 0) innerIdsByOuter[msg.tool_call_id] = inner
  }

  const ownedByMsg: Record<string, Set<string>> = {}
  for (const msg of messages) {
    if (msg.role !== 'assistant') continue
    const owned = new Set<string>()
    for (const block of msg.content) {
      if (block.type !== 'tool_call') continue
      owned.add(block.id)
      if (block.name === 'subagent') {
        const inner = innerIdsByOuter[block.id]
        if (inner) for (const id of inner) owned.add(id)
      }
    }
    if (owned.size > 0) ownedByMsg[msg.id] = owned
  }
  return ownedByMsg
}

const EMPTY: Record<string, ToolResultEntry> = Object.freeze({}) as Record<string, ToolResultEntry>

/**
 * Per-message subset of (live ?? historical) tool-result entries, keyed by
 * message id. Each per-message subset keeps the same object reference across
 * renders unless one of that message's own tool_call_ids gained or changed
 * an entry — so a `tool_result` for tool_call X only forces a re-render of
 * the historical bubble that actually carries X, leaving every other
 * memo'd history message alone.
 *
 * Live wins over historical when both have an entry for the same id: handles
 * the `__commitTurnAndInject` case where an assistant bubble is moved into
 * history with an unresolved tool_call and the later `tool_result` lands in
 * the live store map before the next finalize.
 *
 * The render-time `prevRef.current` reads are deliberate — this is the same
 * render-phase stabilizer pattern as `useStableRecord` in `useMessages.ts`,
 * and `react-hooks/refs` is configured as `warn` not `error` in the repo
 * ESLint config exactly because this pattern shows up in a few places.
 */
export function useMessageScopedToolResults(
  messages: Message[],
  historical: Record<string, ToolResultEntry>,
  live: Record<string, ToolResultEntry>,
): Record<string, Record<string, ToolResultEntry>> {
  const prevRef = useRef<Record<string, Record<string, ToolResultEntry>>>({})

  const ownedIdsByMessage = useMemo(() => buildOwnedToolCallIdsByMessage(messages), [messages])

  /* eslint-disable react-hooks/refs --
   * Deliberate render-phase ref stabilizer pattern (see `useStableRecord` in
   * `useMessages.ts` for prior art). Reading and writing `prevRef.current`
   * during render here gives every consumer a stable per-message subset
   * reference unless that subset's content actually changed, which is the
   * whole point of this hook.
   */
  return useMemo(() => {
    const prev = prevRef.current
    const next: Record<string, Record<string, ToolResultEntry>> = {}
    for (const msg of messages) {
      if (msg.role !== 'assistant') continue
      const owned = ownedIdsByMessage[msg.id]
      if (!owned) {
        next[msg.id] = EMPTY
        continue
      }
      const subset: Record<string, ToolResultEntry> = {}
      let hasAny = false
      for (const id of owned) {
        const entry = live[id] ?? historical[id]
        if (entry) {
          subset[id] = entry
          hasAny = true
        }
      }
      if (!hasAny) {
        next[msg.id] = EMPTY
        continue
      }
      const prevSubset = prev[msg.id]
      const keys = Object.keys(subset)
      const sameShape = prevSubset !== undefined && keys.length === Object.keys(prevSubset).length
      const sameValues = sameShape && keys.every((k) => prevSubset[k] === subset[k])
      next[msg.id] = sameValues ? prevSubset : subset
    }
    prevRef.current = next
    return next
  }, [messages, historical, live, ownedIdsByMessage])
  /* eslint-enable react-hooks/refs */
}
