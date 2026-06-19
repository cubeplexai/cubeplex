'use client'

import { useTranslations } from 'next-intl'

import { cn } from '@/lib/utils'
import { PlatformLogo } from '@/components/im/PlatformLogo'

import { ALL_PLATFORMS } from '../platforms'
import type { PlatformDescriptor } from '../platforms/types'

type DynamicT = (key: string, values?: Record<string, string | number>) => string

interface Props {
  onPick: (descriptor: PlatformDescriptor) => void
}

function comingLabel(_t: DynamicT, _id: string): string {
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
            'flex flex-col items-center gap-2 rounded border p-4 text-sm transition-colors',
            p.live
              ? 'hover:border-primary hover:bg-accent/40 cursor-pointer'
              : 'opacity-40 cursor-not-allowed',
          )}
        >
          <PlatformLogo platform={p.id} className="h-10 w-10" />
          <span className="font-medium">{t(p.labelKey)}</span>
          {!p.live && <span className="text-xs text-muted-foreground">{comingLabel(t, p.id)}</span>}
        </button>
      ))}
    </div>
  )
}
