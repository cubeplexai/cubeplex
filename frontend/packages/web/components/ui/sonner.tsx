'use client'

import { useTheme } from 'next-themes'
import { Toaster as Sonner } from 'sonner'
import { resolveColorMode } from '@/lib/themeFlavor'

export function Toaster(props: React.ComponentProps<typeof Sonner>) {
  const { theme, resolvedTheme } = useTheme()
  return (
    <Sonner
      theme={resolveColorMode(theme, resolvedTheme)}
      position="bottom-right"
      toastOptions={{
        classNames: {
          toast: 'bg-raised border border-border-strong text-foreground rounded-lg shadow-lg',
          actionButton: 'bg-primary text-primary-foreground',
        },
      }}
      {...props}
    />
  )
}
