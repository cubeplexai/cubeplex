'use client'

import { useTheme } from 'next-themes'
import { Toaster as Sonner } from 'sonner'

export function Toaster(props: React.ComponentProps<typeof Sonner>) {
  const { resolvedTheme } = useTheme()
  return (
    <Sonner
      theme={resolvedTheme === 'dark' ? 'dark' : 'light'}
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
