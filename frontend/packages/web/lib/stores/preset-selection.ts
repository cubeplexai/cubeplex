/**
 * Per-workspace Zustand store for the chat composer's preset + thinking
 * selection.
 *
 * Persisted to localStorage under `preset-selection-v1:${wsId}` so each
 * workspace has its own remembered choice (D4). Only the user's
 * choices persist via `partialize`; the workspace preset list is always
 * refetched on mount and validated against the persisted `presetLabel`.
 */

import { create, type StoreApi, type UseBoundStore } from 'zustand'
import { persist } from 'zustand/middleware'

import type { ThinkingLevel, WorkspacePresetSummary } from '@/lib/types/presets'

export interface PresetSelectionState {
  /** The workspace preset list, refetched on mount. Not persisted. */
  presets: WorkspacePresetSummary[]
  /** Selected preset label; `null` means "use workspace default". */
  presetLabel: string | null
  /** Selected thinking level; default `"off"` (Standard in UI). */
  thinking: ThinkingLevel

  setPresets: (p: WorkspacePresetSummary[]) => void
  setPresetLabel: (l: string | null) => void
  setThinking: (t: ThinkingLevel) => void
  reset: () => void
}

const STORAGE_PREFIX = 'preset-selection-v1:'

function storageKey(wsId: string): string {
  return `${STORAGE_PREFIX}${wsId}`
}

const stores = new Map<string, UseBoundStore<StoreApi<PresetSelectionState>>>()

/**
 * Get (or lazily create) the per-`wsId` Zustand store. The composer is
 * expected to memoize this call by `wsId` so the same hook identity is
 * passed to React across renders.
 */
export function getPresetSelectionStore(
  wsId: string,
): UseBoundStore<StoreApi<PresetSelectionState>> {
  const existing = stores.get(wsId)
  if (existing) return existing

  const store = create<PresetSelectionState>()(
    persist(
      (set) => ({
        presets: [],
        presetLabel: null,
        thinking: 'off' as ThinkingLevel,
        setPresets: (presets) => set({ presets }),
        setPresetLabel: (presetLabel) => set({ presetLabel }),
        setThinking: (thinking) => set({ thinking }),
        reset: () => set({ presetLabel: null, thinking: 'off' as ThinkingLevel }),
      }),
      {
        name: storageKey(wsId),
        // Whitelist: only the user's choices persist. The presets list is
        // always refetched and validated against `presetLabel` on mount.
        partialize: (state) => ({
          presetLabel: state.presetLabel,
          thinking: state.thinking,
        }),
      },
    ),
  )
  stores.set(wsId, store)
  return store
}

/**
 * Logout flow helper: clear every per-`wsId` persisted selection and drop
 * the in-memory store registry so the next login starts fresh.
 */
export function clearAllPresetSelectionStores(): void {
  for (const wsId of stores.keys()) {
    try {
      if (typeof localStorage !== 'undefined') {
        localStorage.removeItem(storageKey(wsId))
      }
    } catch {
      // SSR / privacy mode — best effort.
    }
  }
  stores.clear()
}
