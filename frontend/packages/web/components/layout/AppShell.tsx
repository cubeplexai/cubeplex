'use client'

import { ReactNode } from 'react'
import { ThemeToggle } from '@/components/ui/theme-toggle'
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable'
import { ToolDetailPanel } from '@/components/panel/ToolDetailPanel'
import { ArtifactPanel } from '@/components/panel/artifact/ArtifactPanel'
import { AttachmentPreviewView } from '@/components/panel/AttachmentPreviewView'
import { usePanelStore } from '@cubebox/core'

interface AppShellProps {
  children: ReactNode
  headerTitle?: string
}

export function AppShell({ children, headerTitle }: AppShellProps) {
  const view = usePanelStore((s) => s.view)
  const panelOpen = view.type !== 'closed'

  return (
    <ResizablePanelGroup orientation="horizontal" className="h-full">
      <ResizablePanel defaultSize={panelOpen ? 50 : 100} minSize={30}>
        <div className="flex flex-col h-full overflow-hidden">
          <header className="h-11 border-b border-border bg-card flex items-center px-4 gap-3 shrink-0">
            <span className="op-eyebrow">conversation</span>
            <span className="text-[13px] font-medium text-foreground truncate flex-1 min-w-0">
              {headerTitle || 'Untitled'}
            </span>
            <ThemeToggle />
          </header>
          <main className="flex-1 flex flex-col overflow-hidden">{children}</main>
        </div>
      </ResizablePanel>

      {panelOpen && (
        <>
          <ResizableHandle withHandle />
          <ResizablePanel defaultSize={50} minSize={25}>
            {view.type === 'artifact' ? (
              <ArtifactPanel />
            ) : view.type === 'attachment' ? (
              <AttachmentPreviewView info={view.info} />
            ) : (
              <ToolDetailPanel />
            )}
          </ResizablePanel>
        </>
      )}
    </ResizablePanelGroup>
  )
}
