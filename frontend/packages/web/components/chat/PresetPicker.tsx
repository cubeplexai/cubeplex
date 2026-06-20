'use client'

import { useEffect, useMemo } from 'react'
import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
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
 * validates the persisted `modelPresetKey` against the fresh list, resetting
 * it to `null` (workspace default) if the key no longer exists (D4).
 */
export function PresetPicker({ wsId }: PresetPickerProps): React.ReactElement {
  const t = useTranslations('chat')
  const useStore = useMemo(() => getPresetSelectionStore(wsId), [wsId])
  const presets = useStore((s) => s.presets)
  const modelPresetKey = useStore((s) => s.modelPresetKey)
  const setPresets = useStore((s) => s.setPresets)
  const setModelPresetKey = useStore((s) => s.setModelPresetKey)

  useEffect(() => {
    let cancelled = false
    fetchWorkspaceModelPresets(wsId)
      .then((fresh) => {
        if (cancelled) return
        setPresets(fresh)
        // Validate the persisted choice against the fresh list (D4).
        const valid = new Set(fresh.map((p) => p.key))
        const current = useStore.getState().modelPresetKey
        if (current !== null && !valid.has(current)) {
          setModelPresetKey(null)
        }
      })
      .catch(() => {
        // Swallow — composer shows the placeholder; sending without a
        // preset_label means the backend uses the workspace default.
      })
    return () => {
      cancelled = true
    }
  }, [wsId, setPresets, setModelPresetKey, useStore])

  return (
    <Select
      value={modelPresetKey}
      items={presets.map((p) => ({ value: p.key, label: p.key }))}
      onValueChange={(v) => setModelPresetKey(v ? v : null)}
    >
      <SelectTrigger className="min-w-36" aria-label={t('presetAriaLabel')}>
        <SelectValue placeholder={t('presetPlaceholder')} />
      </SelectTrigger>
      <SelectContent>
        {presets.map((p) => (
          <SelectItem key={p.key} value={p.key}>
            <span className="flex items-center gap-1.5">
              {p.key}
              {p.is_default && (
                <Badge variant="secondary" className="px-1 text-[10px]">
                  {t('defaultPresetBadge')}
                </Badge>
              )}
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
