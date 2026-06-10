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
        // v2 dropped the `minimal` level (deepseek's schema doesn't accept it
        // and we never had a sensible mapping for the other providers either).
        // Rewrite stale persisted values so the dropdown doesn't render an
        // orphan selection after upgrade.
        version: 2,
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
