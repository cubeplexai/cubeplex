'use client'

import { useTranslations } from 'next-intl'
import { Zap } from 'lucide-react'

interface SubAgentClusterProps {
  activeCount: number
  totalCount: number
}

export function SubAgentCluster({ activeCount, totalCount }: SubAgentClusterProps) {
  const t = useTranslations('subagent')
  if (totalCount < 2) return null

  const allDone = activeCount === 0

  return (
    <div className="flex items-center gap-1.5 px-2 py-1 text-xs text-muted-foreground">
      <Zap className={`size-3 ${allDone ? 'text-success-fg' : 'text-primary animate-pulse'}`} />
      <span>
        {t('cluster')}
        <span className="mx-1 text-muted-foreground/40">·</span>
        {allDone ? t('allDone', { count: totalCount }) : t('activeTasks', { count: activeCount })}
      </span>
    </div>
  )
}
