// frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/_components/WarningCell.tsx
'use client'

import { AlertTriangle } from 'lucide-react'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'

interface Props {
  warnings: string[]
}

export function WarningCell({ warnings }: Props) {
  if (warnings.length === 0) return null

  const label = `Blocked by network policy: ${warnings.join(', ')}`

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger>
          <span className="inline-flex items-center">
            <AlertTriangle className="size-3.5 text-amber-500" />
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs text-xs">
          {label}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
