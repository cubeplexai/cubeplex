import { beforeEach, describe, expect, it } from 'vitest'
import { COMPACT_PENDING_DELAY_MS, useCompactionPendingStore } from '../compaction-pending'

describe('compaction pending store', () => {
  beforeEach(() => {
    useCompactionPendingStore.setState({ compactingConversationIds: {} })
  })

  it('marks one conversation and clears it without touching others', () => {
    useCompactionPendingStore.getState().beginCompacting('conv-1')
    useCompactionPendingStore.getState().beginCompacting('conv-1')
    useCompactionPendingStore.getState().beginCompacting('conv-2')
    expect(useCompactionPendingStore.getState().compactingConversationIds).toEqual({
      'conv-1': true,
      'conv-2': true,
    })

    useCompactionPendingStore.getState().endCompacting('conv-1')
    useCompactionPendingStore.getState().endCompacting('conv-1')
    expect(useCompactionPendingStore.getState().compactingConversationIds).toEqual({
      'conv-2': true,
    })
  })

  it('waits briefly before the waiting line can appear', () => {
    expect(COMPACT_PENDING_DELAY_MS).toBe(500)
  })
})
