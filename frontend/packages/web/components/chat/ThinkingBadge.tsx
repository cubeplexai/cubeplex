'use client'

import { useMemo } from 'react'

import { getPresetSelectionStore } from '@/lib/stores/preset-selection'

interface ThinkingBadgeProps {
  wsId: string
}

/**
 * Inline chip rendered whenever the thinking level is non-`off`. Always
 * visible so the user can't forget a "high" setting and burn budget on
 * subsequent messages (D5).
 */
export function ThinkingBadge({ wsId }: ThinkingBadgeProps): React.ReactElement | null {
  const useStore = useMemo(() => getPresetSelectionStore(wsId), [wsId])
  const thinking = useStore((s) => s.thinking)

  if (thinking === 'off') return null
  return (
    <span
      role="status"
      aria-label={`Thinking level ${thinking}`}
      className="rounded bg-warning-surface border border-warning-border px-1.5 py-0.5 text-xs font-medium text-warning-fg"
    >
      thinking: {thinking}
    </span>
  )
}
