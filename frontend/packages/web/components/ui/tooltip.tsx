'use client'

import * as React from 'react'
import { cn } from '@/lib/utils'

function TooltipProvider({ children }: { children: React.ReactNode; delay?: number }) {
  return <>{children}</>
}

function Tooltip({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}

function TooltipTrigger({ children, ...props }: React.ComponentProps<'button'>) {
  return (
    <button data-slot="tooltip-trigger" type="button" {...props}>
      {children}
    </button>
  )
}

function TooltipContent({
  className,
  children,
  ...props
}: React.ComponentProps<'div'> & {
  side?: string
  sideOffset?: number
  align?: string
  alignOffset?: number
}) {
  return (
    <div
      data-slot="tooltip-content"
      className={cn(
        'z-50 inline-flex w-fit max-w-xs items-center gap-1.5 rounded-lg bg-foreground px-3 py-1.5 text-xs text-background',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  )
}

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider }
