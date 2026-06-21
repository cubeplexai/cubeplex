import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  clearAllPresetSelectionStores,
  consumeLocallyCreatedConversation,
  getPresetSelectionStore,
  markConversationLocallyCreated,
  validatedModelKey,
  type PresetSelectionState,
} from '@/lib/stores/preset-selection'

function resetWorld(): void {
  clearAllPresetSelectionStores()
  localStorage.clear()
}

beforeEach(() => {
  resetWorld()
})

afterEach(() => {
  resetWorld()
})

describe('preset-selection store', () => {
  it('returns the same store instance for the same wsId', () => {
    const a = getPresetSelectionStore('ws_1')
    const b = getPresetSelectionStore('ws_1')
    expect(a).toBe(b)
  })

  it('isolates state between distinct wsIds', () => {
    const wsA = getPresetSelectionStore('ws_a')
    const wsB = getPresetSelectionStore('ws_b')
    wsA.getState().setModelKey('alpha')
    wsB.getState().setModelKey('beta')
    expect(wsA.getState().modelKey).toBe('alpha')
    expect(wsB.getState().modelKey).toBe('beta')
  })

  it('persists modelKey, thinking, and the cached presets list', () => {
    const ws = getPresetSelectionStore('ws_persist')
    ws.getState().setPresets([
      {
        key: 'pro',
        kind: 'tier',
        primary: 'anthropic/claude-opus-4-7',
        description: '',
        is_default: true,
      },
      { key: 'lite', kind: 'tier', primary: 'openai/gpt-5', description: '', is_default: false },
    ])
    ws.getState().setModelKey('lite')
    ws.getState().setThinking('high')

    const raw = localStorage.getItem('preset-selection-v1:ws_persist')
    expect(raw).not.toBeNull()
    const parsed = JSON.parse(raw as string) as { state: Record<string, unknown> }
    expect(parsed.state.modelKey).toBe('lite')
    expect(parsed.state.thinking).toBe('high')
    // Presets are cached so the composer renders the model name on the next
    // mount without waiting for the refetch (stale-while-revalidate).
    expect(parsed.state.presets).toHaveLength(2)
  })

  it('uses a wsId-scoped storage key', () => {
    const ws = getPresetSelectionStore('ws_keyed')
    ws.getState().setThinking('medium')
    expect(localStorage.getItem('preset-selection-v1:ws_keyed')).not.toBeNull()
    expect(localStorage.getItem('preset-selection-v1:ws_other')).toBeNull()
  })

  it('reset() returns selections to defaults but leaves presets', () => {
    const ws = getPresetSelectionStore('ws_reset')
    ws.getState().setPresets([
      {
        key: 'pro',
        kind: 'tier',
        primary: 'anthropic/claude-opus-4-7',
        description: '',
        is_default: true,
      },
    ])
    ws.getState().setModelKey('pro')
    ws.getState().setThinking('high')
    ws.getState().reset()
    const st = ws.getState()
    expect(st.modelKey).toBeNull()
    expect(st.thinking).toBe('medium')
    expect(st.presets).toHaveLength(1)
  })

  it('clearAllPresetSelectionStores() removes persisted keys and registry', () => {
    const a = getPresetSelectionStore('ws_a')
    const b = getPresetSelectionStore('ws_b')
    a.getState().setModelKey('x')
    b.getState().setModelKey('y')
    expect(localStorage.getItem('preset-selection-v1:ws_a')).not.toBeNull()
    expect(localStorage.getItem('preset-selection-v1:ws_b')).not.toBeNull()

    clearAllPresetSelectionStores()

    expect(localStorage.getItem('preset-selection-v1:ws_a')).toBeNull()
    expect(localStorage.getItem('preset-selection-v1:ws_b')).toBeNull()
    // New call after clear returns a fresh store instance.
    const aAfter = getPresetSelectionStore('ws_a')
    expect(aAfter).not.toBe(a)
    expect(aAfter.getState().modelKey).toBeNull()
  })

  it('clearAllPresetSelectionStores() wipes prefix-matched entries written outside this tab', () => {
    // Simulate persisted entries from another tab / earlier session whose
    // stores were never instantiated in this tab — the in-memory `stores`
    // map is empty for these keys, but the localStorage records exist.
    localStorage.setItem(
      'preset-selection-v1:ws_other_tab',
      JSON.stringify({ state: { modelKey: 'leaked', thinking: 'high' }, version: 0 }),
    )
    localStorage.setItem(
      'preset-selection-v1:ws_another',
      JSON.stringify({ state: { modelKey: 'also-leaked', thinking: 'off' }, version: 0 }),
    )
    // An unrelated key must survive.
    localStorage.setItem('not-our-key', 'keep-me')

    clearAllPresetSelectionStores()

    expect(localStorage.getItem('preset-selection-v1:ws_other_tab')).toBeNull()
    expect(localStorage.getItem('preset-selection-v1:ws_another')).toBeNull()
    expect(localStorage.getItem('not-our-key')).toBe('keep-me')
  })

  describe('validatedModelKey', () => {
    const make = (modelKey: string | null, keys: string[]): PresetSelectionState =>
      ({
        modelKey,
        thinking: 'medium',
        presets: keys.map((key) => ({
          key,
          kind: 'tier' as const,
          primary: 'p/m',
          description: '',
          is_default: false,
        })),
      }) as PresetSelectionState

    it('keeps a key that exists in the current preset list', () => {
      expect(validatedModelKey(make('pro', ['lite', 'pro']))).toBe('pro')
    })

    it('coerces a stale/unknown key to null (would 400 on send)', () => {
      expect(validatedModelKey(make('research', ['lite', 'pro']))).toBeNull()
    })

    it('coerces to null when presets are not loaded yet', () => {
      expect(validatedModelKey(make('pro', []))).toBeNull()
    })

    it('passes null through (workspace default)', () => {
      expect(validatedModelKey(make(null, ['lite', 'pro']))).toBeNull()
    })
  })

  describe('locally-created conversation marker', () => {
    it('consumes the marker exactly once (skip first open-sync, then sync)', () => {
      markConversationLocallyCreated('conv_new')
      expect(consumeLocallyCreatedConversation('conv_new')).toBe(true)
      // Already consumed: a later genuine re-open syncs normally.
      expect(consumeLocallyCreatedConversation('conv_new')).toBe(false)
    })

    it('returns false for a conversation that was not locally created', () => {
      expect(consumeLocallyCreatedConversation('conv_existing')).toBe(false)
    })
  })
})
