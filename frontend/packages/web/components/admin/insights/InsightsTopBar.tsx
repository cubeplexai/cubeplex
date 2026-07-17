'use client'

import { useTranslations } from 'next-intl'
import { buildExportUrl } from '@cubeplex/core'
import { buttonVariants } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { Download } from 'lucide-react'

interface Props {
  fromDate: string
  toDate: string
}

export function InsightsTopBar({ fromDate, toDate }: Props) {
  const t = useTranslations('adminInsights')
  return (
    <div className="flex items-center justify-between px-4 py-3 border-b border-border/70">
      <div>
        <h1 className="text-sm font-semibold">{t('heading')}</h1>
        <p className="text-xs text-muted-foreground">{`${fromDate} — ${toDate}`}</p>
      </div>
      <a
        href={buildExportUrl(undefined, { from: fromDate, to: toDate })}
        download
        className={cn(buttonVariants({ variant: 'default', size: 'sm' }))}
      >
        <Download className="size-3.5 mr-1.5" />
        {t('exportOrgCsv')}
      </a>
    </div>
  )
}
