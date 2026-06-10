'use client'

import { ReactNode, useEffect, useRef, useState } from 'react'
import { Monitor } from 'lucide-react'
import { ThemeToggle } from '@/components/ui/theme-toggle'
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable'
import { ToolDetailPanel } from '@/components/panel/ToolDetailPanel'
import { ArtifactPanel } from '@/components/panel/artifact/ArtifactPanel'
import { AttachmentPreviewView } from '@/components/panel/AttachmentPreviewView'
import { BrowserView } from '@/components/panel/BrowserView'
import { SkillCandidatePanel } from '@/components/panel/SkillCandidatePanel'
import { cn } from '@/lib/utils'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { usePanelStore } from '@cubebox/core'
import { useDeploymentMode } from '@cubebox/core/hooks/useDeploymentMode'

interface AppShellProps {
  children: ReactNode
  headerTitle?: string
}

export function AppShell({ children, headerTitle }: AppShellProps) {
  const view = usePanelStore((s) => s.view)
  const openBrowser = usePanelStore((s) => s.openBrowser)
  const { workspaceId } = useWorkspaceContext()
  // Only offer the browser panel where the backend actually mounts /browser/*
  // (sandbox support enabled); otherwise the button opens a panel that 404s.
  const { sandboxEnabled } = useDeploymentMode()
  const panelOpen = view.type !== 'closed'
  // DOM-level drag detection on the resize handle; CSS disables the
  // width transition while .panel-dragging is set so resize stays 1:1.
  const groupRef = useRef<HTMLDivElement>(null)
  const [dragging, setDragging] = useState(false)
  useEffect(() => {
    const root = groupRef.current
    if (!root) return
    const handle = root.querySelector<HTMLElement>('[data-slot="resizable-handle"]')
    if (!handle) return
    const onDown = () => setDragging(true)
    const onUp = () => setDragging(false)
    handle.addEventListener('pointerdown', onDown)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    return () => {
      handle.removeEventListener('pointerdown', onDown)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
    }
  }, [panelOpen])

  return (
    <ResizablePanelGroup
      orientation="horizontal"
      className={cn('h-full', dragging && 'panel-dragging')}
      elementRef={groupRef}
    >
      <ResizablePanel defaultSize={panelOpen ? 50 : 100} minSize={30}>
        <div className="flex flex-col h-full overflow-hidden">
          <header className="h-11 border-b border-border flex items-center px-4 shrink-0">
            <span className="text-sm text-muted-foreground truncate flex-1">
              {headerTitle || ''}
            </span>
            {workspaceId && sandboxEnabled && (
              <button
                type="button"
                onClick={openBrowser}
                className="mr-1 rounded p-1.5 text-muted-foreground hover:bg-accent"
                aria-label="Open sandbox browser"
                title="Open sandbox browser"
              >
                <Monitor className="h-4 w-4" />
              </button>
            )}
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
            ) : view.type === 'browser' ? (
              <BrowserView workspaceId={workspaceId} />
            ) : view.type === 'skill-candidate' ? (
              <SkillCandidatePanel
                candidateId={view.candidateId}
                repo={view.repo}
                sourceName={view.sourceName}
              />
            ) : (
              <ToolDetailPanel />
            )}
          </ResizablePanel>
        </>
      )}
    </ResizablePanelGroup>
  )
}
