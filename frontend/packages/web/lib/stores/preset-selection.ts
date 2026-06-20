/**
 * Per-workspace Zustand store for the chat composer's preset + thinking
 * selection.
 *
 * Persisted to localStorage under `preset-selection-v1:${wsId}` so each
 * workspace has its own remembered choice (D4). Only the user's
 * choices persist via `partialize`; the workspace preset list is always
 * refetched on mount and validated against the persisted `modelPresetKey`.
 */

import { create, type StoreApi, type UseBoundStore } from 'zustand'
import { persist } from 'zustand/middleware'

import type { ThinkingLevel, WorkspacePresetSummary } from '@/lib/types/presets'

export interface PresetSelectionState {
  /** The workspace preset list, refetched on mount. Not persisted. */
  presets: WorkspacePresetSummary[]
  /** Selected preset key (tier name or custom label); `null` = workspace default. */
  modelPresetKey: string | null
  /** Selected thinking level; default `"off"` (Standard in UI). */
  thinking: ThinkingLevel

  setPresets: (p: WorkspacePresetSummary[]) => void
  setModelPresetKey: (key: string | null) => void
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
        modelPresetKey: null,
        thinking: 'off' as ThinkingLevel,
        setPresets: (presets) => set({ presets }),
        setModelPresetKey: (modelPresetKey) => set({ modelPresetKey }),
        setThinking: (thinking) => set({ thinking }),
        reset: () => set({ modelPresetKey: null, thinking: 'off' as ThinkingLevel }),
      }),
      {
        name: storageKey(wsId),
        // Whitelist: only the user's choices persist. The presets list is
        // always refetched and validated against `modelPresetKey` on mount.
        partialize: (state) => ({
          modelPresetKey: state.modelPresetKey,
          thinking: state.thinking,
        }),
        // v3 renamed the persisted selection field `presetLabel` →
        // `modelPresetKey`. A stale `presetLabel` is simply dropped on read;
        // the PresetPicker re-validates the selection against the fresh key
        // list on mount and resets it to null if unknown, so no key remap is
        // needed. v2 dropped the `minimal` thinking level (deepseek's schema
        // rejects it); rewrite stale values so the dropdown has no orphan.
        version: 3,
        migrate: (persisted, _version) => {
          const p = (persisted as Partial<PresetSelectionState>) ?? {}
          if ((p.thinking as string) === 'minimal') p.thinking = 'low'
          return p as PresetSelectionState
        },
      },
    ),
  )
  stores.set(wsId, store)
  return store
}

/**
 * Logout flow helper: clear every per-`wsId` persisted selection and drop
 * the in-memory store registry so the next login starts fresh.
 *
 * Scans ALL localStorage keys matching our prefix — not just the
 * in-memory `stores.keys()` — so we also wipe entries written by other
 * tabs / earlier sessions whose stores were never instantiated in this
 * tab. Without that sweep, logging in as a different user would see the
 * previous user's persisted preset selections.
 */
export function clearAllPresetSelectionStores(): void {
  for (const wsId of stores.keys()) {
    const store = stores.get(wsId)
    try {
      // Zustand's persist middleware is attached to the store as a `persist`
      // namespace; the type isn't exposed on the public UseBoundStore generic
      // so we read it with an unknown-cast guard.
      const persisted = (
        store as unknown as {
          persist?: { clearStorage?: () => void }
        }
      )?.persist
      persisted?.clearStorage?.()
    } catch {
      // best-effort — fall through to the direct localStorage sweep below.
    }
  }
  stores.clear()

  if (typeof window === 'undefined') return
  try {
    const keysToRemove: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (key?.startsWith(STORAGE_PREFIX)) {
        keysToRemove.push(key)
      }
    }
    for (const k of keysToRemove) {
      localStorage.removeItem(k)
    }
  } catch {
    // SSR / privacy mode — best effort.
  }
}
