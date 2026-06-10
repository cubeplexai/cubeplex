'use client'

import { useMemo } from 'react'
import { useTranslations } from 'next-intl'
import { Brain } from 'lucide-react'

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { getPresetSelectionStore } from '@/lib/stores/preset-selection'
import type { ThinkingLevel } from '@/lib/types/presets'

interface ThinkingControlProps {
  wsId: string
}

const LEVEL_KEYS = [
  { value: 'off', labelKey: 'thinkingLevelOff' },
  { value: 'minimal', labelKey: 'thinkingLevelMinimal' },
  { value: 'low', labelKey: 'thinkingLevelLow' },
  { value: 'medium', labelKey: 'thinkingLevelMedium' },
  { value: 'high', labelKey: 'thinkingLevelHigh' },
  { value: 'xhigh', labelKey: 'thinkingLevelXhigh' },
] as const satisfies readonly { value: ThinkingLevel; labelKey: string }[]

/**
 * Composer dropdown for the thinking depth. Sticky across messages (D5);
 * the inline `ThinkingBadge` keeps an elevated level visible to avoid
 * silent bill shock.
 */
export function ThinkingControl({ wsId }: ThinkingControlProps): React.ReactElement {
  const t = useTranslations('chat')
  const useStore = useMemo(() => getPresetSelectionStore(wsId), [wsId])
  const thinking = useStore((s) => s.thinking)
  const setThinking = useStore((s) => s.setThinking)

  const items = LEVEL_KEYS.map((l) => ({ value: l.value, label: t(l.labelKey) }))

  return (
    <Select
      value={thinking}
      items={items}
      onValueChange={(v) => setThinking((v ?? 'off') as ThinkingLevel)}
    >
      <SelectTrigger className="min-w-32" aria-label={t('thinkingAriaLabel')}>
        <Brain aria-hidden className="size-3.5 text-muted-foreground" />
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {items.map((l) => (
          <SelectItem key={l.value} value={l.value}>
            {l.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
