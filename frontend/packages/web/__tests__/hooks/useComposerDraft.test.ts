import { describe, expect, it, beforeEach } from 'vitest'
import { useComposerDraft } from '@/hooks/useComposerDraft'

describe('useComposerDraft', () => {
  beforeEach(() => {
    useComposerDraft.setState({ draft: null })
  })

  it('setDraft stores the text', () => {
    useComposerDraft.getState().setDraft('hello')
    expect(useComposerDraft.getState().draft).toBe('hello')
  })

  it('consume returns the draft and clears it', () => {
    useComposerDraft.getState().setDraft('task X')
    expect(useComposerDraft.getState().consume()).toBe('task X')
    expect(useComposerDraft.getState().draft).toBeNull()
  })

  it('consume returns null when no draft pending', () => {
    expect(useComposerDraft.getState().consume()).toBeNull()
  })
})
