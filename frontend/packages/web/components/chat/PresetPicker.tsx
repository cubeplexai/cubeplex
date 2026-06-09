'use client'

import { useEffect, useMemo } from 'react'

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { fetchWorkspaceModelPresets } from '@/lib/api/presets'
import { getPresetSelectionStore } from '@/lib/stores/preset-selection'

interface PresetPickerProps {
  wsId: string
}

/**
 * Composer dropdown for picking a model preset. Backed by the per-`wsId`
 * Zustand store. On mount it refetches the workspace preset list and
 * validates the persisted `presetLabel` against the fresh list, resetting
 * it to `null` (workspace default) if the label no longer exists (D4).
 */
export function PresetPicker({ wsId }: PresetPickerProps): React.ReactElement {
  const useStore = useMemo(() => getPresetSelectionStore(wsId), [wsId])
  const presets = useStore((s) => s.presets)
  const presetLabel = useStore((s) => s.presetLabel)
  const setPresets = useStore((s) => s.setPresets)
  const setPresetLabel = useStore((s) => s.setPresetLabel)

  useEffect(() => {
    let cancelled = false
    fetchWorkspaceModelPresets(wsId)
      .then((fresh) => {
        if (cancelled) return
        setPresets(fresh)
        // Validate the persisted choice against the fresh list (D4).
        const valid = new Set(fresh.map((p) => p.label))
        const current = useStore.getState().presetLabel
        if (current !== null && !valid.has(current)) {
          setPresetLabel(null)
        }
      })
      .catch(() => {
        // Swallow — composer shows the placeholder; sending without a
        // preset_label means the backend uses the workspace default.
      })
    return () => {
      cancelled = true
    }
  }, [wsId, setPresets, setPresetLabel, useStore])

  return (
    <Select value={presetLabel ?? ''} onValueChange={(v) => setPresetLabel(v ? v : null)}>
      <SelectTrigger className="w-36" aria-label="Model preset">
        <SelectValue placeholder="Preset" />
      </SelectTrigger>
      <SelectContent>
        {presets.map((p) => (
          <SelectItem key={p.label} value={p.label}>
            {p.label}
            {p.is_default ? ' (default)' : ''}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
