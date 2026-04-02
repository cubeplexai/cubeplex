'use client'

import { ReactNode } from 'react'
import { Sidebar } from './Sidebar'
import { ThemeToggle } from '@/components/ui/theme-toggle'
import {
  ResizablePanelGroup,
  ResizablePanel,
  ResizableHandle,
} from '@/components/ui/resizable'
import { ToolDetailPanel } from '@/components/panel/ToolDetailPanel'
import { useToolDetail } from '@/hooks/useToolDetail'

interface AppShellProps {
  children: ReactNode
  headerTitle?: string
}

export function AppShell({ children, headerTitle }: AppShellProps) {
  const { isOpen } = useToolDetail()

  return (
    <div className="flex h-screen bg-background text-foreground">
      <Sidebar />
      <ResizablePanelGroup orientation="horizontal">
        <ResizablePanel defaultSize={isOpen ? 50 : 100} minSize={30}>
          <div className="flex flex-col h-full overflow-hidden">
            <header className="h-11 border-b border-border flex items-center px-4 shrink-0">
              <span className="text-sm text-muted-foreground truncate flex-1">
                {headerTitle || ''}
              </span>
              <ThemeToggle />
            </header>
            <main className="flex-1 flex flex-col overflow-hidden">{children}</main>
          </div>
        </ResizablePanel>

        {isOpen && (
          <>
            <ResizableHandle withHandle />
            <ResizablePanel defaultSize={50} minSize={25}>
              <ToolDetailPanel />
            </ResizablePanel>
          </>
        )}
      </ResizablePanelGroup>
    </div>
  )
}
