'use client'

import { useTranslations } from 'next-intl'
import { Package } from 'lucide-react'

export function ArtifactsEmptyState(): React.ReactElement {
  const t = useTranslations('artifactsPage')
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 py-24 text-center">
      <div className="flex size-14 items-center justify-center rounded-2xl bg-muted">
        <Package className="size-7 text-muted-foreground" />
      </div>
      <p className="text-sm font-medium text-foreground">{t('empty')}</p>
      <p className="max-w-xs text-xs text-muted-foreground">{t('emptyHint')}</p>
    </div>
  )
}
