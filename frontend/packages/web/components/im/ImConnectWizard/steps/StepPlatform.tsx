'use client'

import { useTranslations } from 'next-intl'

import { cn } from '@/lib/utils'

import { ALL_PLATFORMS } from '../platforms'
import type { PlatformDescriptor } from '../platforms/types'

type DynamicT = (key: string, values?: Record<string, string | number>) => string

interface Props {
  onPick: (descriptor: PlatformDescriptor) => void
}

function comingLabel(t: DynamicT, id: PlatformDescriptor['id']): string {
  if (id === 'slack') return t('im.platform.slack.coming')
  if (id === 'teams') return t('im.platform.teams.coming')
  return ''
}

export function StepPlatform({ onPick }: Props): React.ReactElement {
  const t = useTranslations() as unknown as DynamicT
  return (
    <div className="grid grid-cols-3 gap-3">
      {ALL_PLATFORMS.map((p) => (
        <button
          key={p.id}
          type="button"
          aria-disabled={!p.live}
          disabled={!p.live}
          onClick={() => p.live && onPick(p)}
          className={cn(
            'flex flex-col items-center gap-1 rounded border p-4 text-sm',
            p.live ? 'hover:border-primary' : 'opacity-40 cursor-not-allowed',
          )}
        >
          <span className="font-medium">{t(p.labelKey)}</span>
          {!p.live && <span className="text-xs text-muted-foreground">{comingLabel(t, p.id)}</span>}
        </button>
      ))}
    </div>
  )
}
