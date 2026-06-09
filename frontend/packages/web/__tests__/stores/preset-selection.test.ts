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
    wsA.getState().setPresetLabel('alpha')
    wsB.getState().setPresetLabel('beta')
    expect(wsA.getState().presetLabel).toBe('alpha')
    expect(wsB.getState().presetLabel).toBe('beta')
  })

  it('persists only presetLabel + thinking (partialize whitelist)', () => {
    const ws = getPresetSelectionStore('ws_persist')
    ws.getState().setPresets([
      { label: 'main', is_default: true },
      { label: 'mini', is_default: false },
    ])
    ws.getState().setPresetLabel('mini')
    ws.getState().setThinking('high')

    const raw = localStorage.getItem('preset-selection-v1:ws_persist')
    expect(raw).not.toBeNull()
    const parsed = JSON.parse(raw as string) as { state: Record<string, unknown> }
    expect(parsed.state).toEqual({ presetLabel: 'mini', thinking: 'high' })
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
    ws.getState().setPresets([{ label: 'main', is_default: true }])
    ws.getState().setPresetLabel('main')
    ws.getState().setThinking('high')
    ws.getState().reset()
    const st = ws.getState()
    expect(st.presetLabel).toBeNull()
    expect(st.thinking).toBe('off')
    expect(st.presets).toHaveLength(1)
  })

  it('clearAllPresetSelectionStores() removes persisted keys and registry', () => {
    const a = getPresetSelectionStore('ws_a')
    const b = getPresetSelectionStore('ws_b')
    a.getState().setPresetLabel('x')
    b.getState().setPresetLabel('y')
    expect(localStorage.getItem('preset-selection-v1:ws_a')).not.toBeNull()
    expect(localStorage.getItem('preset-selection-v1:ws_b')).not.toBeNull()

    clearAllPresetSelectionStores()

    expect(localStorage.getItem('preset-selection-v1:ws_a')).toBeNull()
    expect(localStorage.getItem('preset-selection-v1:ws_b')).toBeNull()
    // New call after clear returns a fresh store instance.
    const aAfter = getPresetSelectionStore('ws_a')
    expect(aAfter).not.toBe(a)
    expect(aAfter.getState().presetLabel).toBeNull()
  })
})
