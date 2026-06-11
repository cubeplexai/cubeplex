import { describe, expect, it, beforeEach } from 'vitest'
import { useComposerDraft } from '@/hooks/useComposerDraft'

describe('useComposerDraft', () => {
  beforeEach(() => {
    useComposerDraft.setState({ pending: null })
  })

  it('setDraft stores the text', () => {
    useComposerDraft.getState().setDraft('hello')
    expect(useComposerDraft.getState().pending?.text).toBe('hello')
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
