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
    <div className="flex flex-wrap justify-center gap-3">
      {ALL_PLATFORMS.map((p) => (
        <button
          key={p.id}
          type="button"
          aria-disabled={!p.live}
          disabled={!p.live}
          onClick={() => p.live && onPick(p)}
          className={cn(
            'flex w-28 flex-col items-center gap-2 rounded-xl border px-5 py-4 transition-all',
            p.live
              ? 'cursor-pointer border-border/70 bg-card/60 shadow-sm hover:border-primary/40 hover:bg-accent hover:shadow-md active:scale-[0.98]'
              : 'cursor-not-allowed border-border/40 bg-muted/20 opacity-60',
          )}
        >
          <PlatformLogo platform={p.id} className="size-8" />
          <span className="text-xs font-medium text-foreground">{t(p.labelKey)}</span>
          {!p.live && (
            <span className="text-[10px] text-muted-foreground/60">{comingLabel(t, p.id)}</span>
          )}
        </button>
      ))}
    </div>
  )
}
