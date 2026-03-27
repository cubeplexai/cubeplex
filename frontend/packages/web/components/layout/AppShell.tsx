'use client'

import { ReactNode } from 'react'
import { Sidebar } from './Sidebar'
import { ThemeToggle } from '@/components/ui/theme-toggle'

interface AppShellProps {
  children: ReactNode
  headerTitle?: string
}

export function AppShell({ children, headerTitle }: AppShellProps) {
  return (
    <div className="flex h-screen bg-background text-foreground">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="h-11 border-b border-border flex items-center px-4 shrink-0">
          <span className="text-sm text-muted-foreground truncate flex-1">
            {headerTitle || ''}
          </span>
          <ThemeToggle />
        </header>
        <main className="flex-1 flex flex-col overflow-hidden">{children}</main>
      </div>
    </div>
  )
}
