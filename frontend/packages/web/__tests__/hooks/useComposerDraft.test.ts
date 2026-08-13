import { describe, expect, it, beforeEach } from 'vitest'
import { useComposerDraft } from '@/hooks/useComposerDraft'

describe('useComposerDraft', () => {
  beforeEach(() => {
    useComposerDraft.setState({ pending: null, pendingByConversation: {} })
  })

  it('setDraft stores the text', () => {
    useComposerDraft.getState().setDraft('hello')
    expect(useComposerDraft.getState().pending?.text).toBe('hello')
  })

  it('keeps a conversation draft pending until that conversation consumes it', () => {
    useComposerDraft.getState().setDraft('restore in A', 'conv-a')

    expect(useComposerDraft.getState().consume('conv-b')).toBeNull()
    expect(useComposerDraft.getState().pendingByConversation['conv-a']?.text).toBe('restore in A')
    expect(useComposerDraft.getState().consume('conv-a')).toBe('restore in A')
    expect(useComposerDraft.getState().pendingByConversation['conv-a']).toBeUndefined()
  })

  it('does not overwrite a scoped recovery draft with an unrelated global draft', () => {
    useComposerDraft.getState().setDraft('restore in A', 'conv-a')
    useComposerDraft.getState().setDraft('prompt in B')

    expect(useComposerDraft.getState().consume('conv-b')).toBe('prompt in B')
    expect(useComposerDraft.getState().consume('conv-a')).toBe('restore in A')
  })

  it('preserves every prepend recovery queued for the same conversation', () => {
    useComposerDraft.getState().setDraft('first recovery', 'conv-a', 'prepend')
    useComposerDraft.getState().setDraft('second recovery', 'conv-a', 'prepend')

    expect(useComposerDraft.getState().consume('conv-a')).toBe('second recovery\nfirst recovery')
  })

  it('consume returns the draft and clears it', () => {
    useComposerDraft.getState().setDraft('task X')
    expect(useComposerDraft.getState().consume()).toBe('task X')
    expect(useComposerDraft.getState().pending).toBeNull()
  })

  it('consume returns null when no draft pending', () => {
    expect(useComposerDraft.getState().consume()).toBeNull()
  })

  it('setDraft increments nonce on every call, so identical text re-fires', () => {
    const store = useComposerDraft.getState()
    store.setDraft('analyze data')
    const first = useComposerDraft.getState().pending
    store.consume() // simulate the consumer eating it
    store.setDraft('analyze data') // identical text, second click
    const second = useComposerDraft.getState().pending
    expect(first?.nonce).toBeDefined()
    expect(second?.nonce).toBeGreaterThan(first!.nonce)
    expect(second?.text).toBe('analyze data')
  })
})
