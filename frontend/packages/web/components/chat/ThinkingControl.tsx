'use client'

import { useMemo } from 'react'

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

interface LevelOption {
  value: ThinkingLevel
  label: string
}

const LEVELS: LevelOption[] = [
  { value: 'off', label: 'Standard' },
  { value: 'minimal', label: 'Minimal' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'xhigh', label: 'Extra High' },
]

/**
 * Composer dropdown for the thinking depth. Sticky across messages (D5);
 * the inline `ThinkingBadge` keeps an elevated level visible to avoid
 * silent bill shock.
 */
export function ThinkingControl({ wsId }: ThinkingControlProps): React.ReactElement {
  const useStore = useMemo(() => getPresetSelectionStore(wsId), [wsId])
  const thinking = useStore((s) => s.thinking)
  const setThinking = useStore((s) => s.setThinking)

  return (
    <Select value={thinking} onValueChange={(v) => setThinking((v ?? 'off') as ThinkingLevel)}>
      <SelectTrigger className="w-32" aria-label="Thinking level">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {LEVELS.map((l) => (
          <SelectItem key={l.value} value={l.value}>
            {l.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
