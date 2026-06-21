import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  clearAllPresetSelectionStores,
  getPresetSelectionStore,
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
    wsA.getState().setModelPresetKey('alpha')
    wsB.getState().setModelPresetKey('beta')
    expect(wsA.getState().modelPresetKey).toBe('alpha')
    expect(wsB.getState().modelPresetKey).toBe('beta')
  })

  it('persists only modelPresetKey + thinking (partialize whitelist)', () => {
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
    ws.getState().setModelPresetKey('lite')
    ws.getState().setThinking('high')

    const raw = localStorage.getItem('preset-selection-v1:ws_persist')
    expect(raw).not.toBeNull()
    const parsed = JSON.parse(raw as string) as { state: Record<string, unknown> }
    expect(parsed.state).toEqual({ modelPresetKey: 'lite', thinking: 'high' })
    // The presets list must not be persisted — it is refetched on mount.
    expect(parsed.state.presets).toBeUndefined()
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
    ws.getState().setModelPresetKey('pro')
    ws.getState().setThinking('high')
    ws.getState().reset()
    const st = ws.getState()
    expect(st.modelPresetKey).toBeNull()
    expect(st.thinking).toBe('medium')
    expect(st.presets).toHaveLength(1)
  })

  it('clearAllPresetSelectionStores() removes persisted keys and registry', () => {
    const a = getPresetSelectionStore('ws_a')
    const b = getPresetSelectionStore('ws_b')
    a.getState().setModelPresetKey('x')
    b.getState().setModelPresetKey('y')
    expect(localStorage.getItem('preset-selection-v1:ws_a')).not.toBeNull()
    expect(localStorage.getItem('preset-selection-v1:ws_b')).not.toBeNull()

    clearAllPresetSelectionStores()

    expect(localStorage.getItem('preset-selection-v1:ws_a')).toBeNull()
    expect(localStorage.getItem('preset-selection-v1:ws_b')).toBeNull()
    // New call after clear returns a fresh store instance.
    const aAfter = getPresetSelectionStore('ws_a')
    expect(aAfter).not.toBe(a)
    expect(aAfter.getState().modelPresetKey).toBeNull()
  })

  it('clearAllPresetSelectionStores() wipes prefix-matched entries written outside this tab', () => {
    // Simulate persisted entries from another tab / earlier session whose
    // stores were never instantiated in this tab — the in-memory `stores`
    // map is empty for these keys, but the localStorage records exist.
    localStorage.setItem(
      'preset-selection-v1:ws_other_tab',
      JSON.stringify({ state: { modelPresetKey: 'leaked', thinking: 'high' }, version: 0 }),
    )
    localStorage.setItem(
      'preset-selection-v1:ws_another',
      JSON.stringify({ state: { modelPresetKey: 'also-leaked', thinking: 'off' }, version: 0 }),
    )
    // An unrelated key must survive.
    localStorage.setItem('not-our-key', 'keep-me')

    clearAllPresetSelectionStores()

    expect(localStorage.getItem('preset-selection-v1:ws_other_tab')).toBeNull()
    expect(localStorage.getItem('preset-selection-v1:ws_another')).toBeNull()
    expect(localStorage.getItem('not-our-key')).toBe('keep-me')
  })
})
