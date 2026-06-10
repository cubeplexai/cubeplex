// frontend/packages/web/components/sandbox-env/WarningCell.tsx
'use client'

import { AlertTriangle } from 'lucide-react'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

interface Props {
  warnings: string[]
}

export function WarningCell({ warnings }: Props) {
  if (warnings.length === 0) return null

  const label = `Blocked by network policy: ${warnings.join(', ')}`

  return (
    <Tooltip>
      <TooltipTrigger className="inline-flex items-center">
        <AlertTriangle className="size-3.5 text-warning-fg" />
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs text-xs">
        {label}
      </TooltipContent>
    </Tooltip>
  )
}
