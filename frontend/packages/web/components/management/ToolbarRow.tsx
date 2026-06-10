import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface ToolbarRowProps {
  /** search input element */
  search?: ReactNode
  /** segmented filters (ui/tabs) */
  filters?: ReactNode
  /** trailing extras */
  children?: ReactNode
  className?: string
}

export function ToolbarRow({ search, filters, children, className }: ToolbarRowProps) {
  return (
    <div className={cn('flex items-center gap-3 px-6 py-3 border-b border-border', className)}>
      {search && <div className="flex-1 max-w-md">{search}</div>}
      {filters}
      {children && <div className="ml-auto flex items-center gap-2">{children}</div>}
    </div>
  )
}
