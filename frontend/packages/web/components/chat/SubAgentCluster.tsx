'use client'

import { Zap } from 'lucide-react'

interface SubAgentClusterProps {
  activeCount: number
  totalCount: number
}

export function SubAgentCluster({ activeCount, totalCount }: SubAgentClusterProps) {
  if (totalCount < 2) return null

  const allDone = activeCount === 0

  return (
    <div className="flex items-center gap-1.5 px-2 py-1 text-xs text-muted-foreground">
      <Zap className={`size-3 ${allDone ? 'text-emerald-500' : 'text-primary animate-pulse'}`} />
      <span>
        Agent 集群
        <span className="mx-1 text-muted-foreground/40">·</span>
        {allDone ? `${totalCount} 个任务已完成` : `${activeCount} 个并行任务`}
      </span>
    </div>
  )
}
