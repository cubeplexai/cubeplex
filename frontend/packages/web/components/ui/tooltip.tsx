'use client'

import * as React from 'react'
import { Tooltip as BaseTooltip } from '@base-ui/react'
import { cn } from '@/lib/utils'

function TooltipProvider({ children, delay = 400 }: { children: React.ReactNode; delay?: number }) {
  return <BaseTooltip.Provider delay={delay}>{children}</BaseTooltip.Provider>
}

function Tooltip({ children }: { children: React.ReactNode }) {
  return <BaseTooltip.Root>{children}</BaseTooltip.Root>
}

function TooltipTrigger({
  children,
  className,
  ...props
}: React.ComponentPropsWithoutRef<'button'>) {
  return (
    <BaseTooltip.Trigger className={className} {...props}>
      {children}
    </BaseTooltip.Trigger>
  )
}

function TooltipContent({
  className,
  children,
  side = 'top',
  sideOffset = 6,
}: {
  className?: string
  children: React.ReactNode
  side?: 'top' | 'right' | 'bottom' | 'left'
  sideOffset?: number
  align?: string
  alignOffset?: number
}) {
  return (
    <BaseTooltip.Portal>
      <BaseTooltip.Positioner side={side} sideOffset={sideOffset}>
        <BaseTooltip.Popup
          className={cn(
            'z-50 w-fit max-w-xs rounded-md bg-foreground px-2.5 py-1 text-xs text-background shadow-md',
            'origin-[var(--transform-origin)] transition-opacity duration-100',
            'data-[ending-style]:opacity-0 data-[starting-style]:opacity-0',
            className,
          )}
        >
          {children}
        </BaseTooltip.Popup>
      </BaseTooltip.Positioner>
    </BaseTooltip.Portal>
  )
}

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider }
