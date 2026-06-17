'use client'

import { ReactNode, useEffect, useRef, useState } from 'react'
import { Menu, Monitor, UserPlus, X } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { ThemeToggle } from '@/components/ui/theme-toggle'
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable'
import { ToolDetailPanel } from '@/components/panel/ToolDetailPanel'
import { ArtifactPanel } from '@/components/panel/artifact/ArtifactPanel'
import { AttachmentPreviewView } from '@/components/panel/AttachmentPreviewView'
import { SandboxPanel } from '@/components/panel/sandbox/SandboxPanel'
import { SkillCandidatePanel } from '@/components/panel/SkillCandidatePanel'
import { cn } from '@/lib/utils'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import { useMobileMenu } from '@/hooks/useMobileMenu'
import { useConversationStore, usePanelStore } from '@cubebox/core'
import { useDeploymentMode } from '@cubebox/core/hooks/useDeploymentMode'
import { SharePanel } from '@/components/chat/SharePanel'
import { ChatHeaderGroupBadge } from '@/components/chat/ChatHeaderGroupBadge'
import { UpgradeToTopicDialog } from '@/components/dialogs/UpgradeToTopicDialog'

interface AppShellProps {
  children: ReactNode
  headerTitle?: string
  conversationId?: string
}

export function AppShell({ children, headerTitle, conversationId }: AppShellProps) {
  const tUpgrade = useTranslations('topics.upgradeDialog')
  const view = usePanelStore((s) => s.view)
  const openSandbox = usePanelStore((s) => s.openSandbox)
  const { workspaceId } = useWorkspaceContext()
  const conversation = useConversationStore((s) =>
    conversationId ? s.conversations.find((c) => c.id === conversationId) : undefined,
  )
  const topicId = conversation?.topic_id ?? null
  const canUpgrade = Boolean(workspaceId && conversation && !conversation.topic_id)
  const [upgradeOpen, setUpgradeOpen] = useState(false)
  // Only offer the browser panel where the backend actually mounts /browser/*
  // (sandbox support enabled); otherwise the button opens a panel that 404s.
  const { sandboxEnabled } = useDeploymentMode()
  const panelOpen = view.type !== 'closed'
  const isSandboxPanel = view.type === 'sandbox'
  // Desktop-first SSR fallback: most users are on desktop, so the mobile
  // overlay branch should not be the first paint on a 1440px session.
  const isDesktop = useMediaQuery('(min-width: 768px)', true)
  const close = usePanelStore((s) => s.close)
  const openMobileMenu = useMobileMenu((s) => s.open)
  // DOM-level drag detection on the resize handle using pointer capture, so
  // pointerup landing inside the right panel's sandboxed iframe (Browser /
  // Widget / Artifact) is still routed back to the handle. window-level
  // listeners alone would leak the dragging state if the iframe captures
  // the pointer.
  const groupRef = useRef<HTMLDivElement>(null)
  const [dragging, setDragging] = useState(false)
  useEffect(() => {
    const root = groupRef.current
    if (!root) return
    const handle = root.querySelector<HTMLElement>('[data-slot="resizable-handle"]')
    if (!handle) return
    const onDown = (e: PointerEvent) => {
      try {
        handle.setPointerCapture(e.pointerId)
      } catch {
        /* capture failure is non-fatal; window fallback still runs */
      }
      setDragging(true)
    }
    const onUp = () => setDragging(false)
    handle.addEventListener('pointerdown', onDown)
    handle.addEventListener('pointerup', onUp)
    handle.addEventListener('lostpointercapture', onUp)
    // Belt-and-suspenders: also catch pointerup at the window in case
    // setPointerCapture is unsupported.
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    return () => {
      handle.removeEventListener('pointerdown', onDown)
      handle.removeEventListener('pointerup', onUp)
      handle.removeEventListener('lostpointercapture', onUp)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
    }
  }, [panelOpen])

  const panelContent =
    view.type === 'artifact' ? (
      <ArtifactPanel />
    ) : view.type === 'attachment' ? (
      <AttachmentPreviewView info={view.info} />
    ) : view.type === 'sandbox' ? (
      <SandboxPanel workspaceId={workspaceId} />
    ) : view.type === 'skill-candidate' ? (
      <SkillCandidatePanel
        candidateId={view.candidateId}
        repo={view.repo}
        sourceName={view.sourceName}
      />
    ) : (
      <ToolDetailPanel />
    )

  const upgradeDialog = workspaceId && conversation && !conversation.topic_id && (
    <UpgradeToTopicDialog
      wsId={workspaceId}
      conversationId={conversation.id}
      initialTitle={conversation.title ?? ''}
      open={upgradeOpen}
      onOpenChange={setUpgradeOpen}
    />
  )

  const main = (
    <div className="flex flex-col h-full overflow-hidden">
      <header className="h-11 border-b border-border flex items-center px-3 md:px-4 shrink-0 gap-1">
        <button
          type="button"
          onClick={openMobileMenu}
          className="md:hidden grid size-7 place-items-center rounded text-muted-foreground hover:bg-accent transition-colors duration-fast"
          aria-label="Open menu"
        >
          <Menu className="size-4" />
        </button>
        <span className="text-sm text-muted-foreground truncate flex-1">{headerTitle || ''}</span>
        {workspaceId && topicId && <ChatHeaderGroupBadge wsId={workspaceId} topicId={topicId} />}
        {canUpgrade && (
          <button
            type="button"
            onClick={() => setUpgradeOpen(true)}
            className="mr-1 rounded p-1.5 text-muted-foreground hover:bg-accent transition-colors duration-fast"
            aria-label={tUpgrade('openLabel')}
            title={tUpgrade('openLabel')}
          >
            <UserPlus className="h-4 w-4" />
          </button>
        )}
        {conversationId && <SharePanel conversationId={conversationId} />}
        {workspaceId && sandboxEnabled && (
          <button
            type="button"
            onClick={openSandbox}
            className="mr-1 rounded p-1.5 text-muted-foreground hover:bg-accent transition-colors duration-fast"
            aria-label="Open sandbox"
            title="Open sandbox"
          >
            <Monitor className="h-4 w-4" />
          </button>
        )}
        <ThemeToggle />
      </header>
      <main className="flex-1 flex flex-col overflow-hidden">{children}</main>
    </div>
  )

  if (!isDesktop) {
    return (
      <div className="relative flex h-full flex-col">
        {main}
        {panelOpen && (
          <div
            className="fixed inset-0 z-40 flex flex-col bg-background animate-in slide-in-from-bottom duration-slow"
            role="dialog"
            aria-modal="true"
          >
            {panelContent}
            <button
              type="button"
              onClick={close}
              className="absolute top-2 right-2 z-50 grid size-8 place-items-center rounded text-muted-foreground hover:bg-accent transition-colors duration-fast"
              aria-label="Close panel"
            >
              <X className="size-4" />
            </button>
          </div>
        )}
        {upgradeDialog}
      </div>
    )
  }

  return (
    <>
      <ResizablePanelGroup
        orientation="horizontal"
        className={cn('h-full', dragging && 'panel-dragging')}
        elementRef={groupRef}
      >
        <ResizablePanel
          defaultSize={panelOpen ? (isSandboxPanel ? 35 : 50) : 100}
          minSize={isSandboxPanel ? 25 : 30}
        >
          {main}
        </ResizablePanel>

        {panelOpen && (
          <>
            <ResizableHandle withHandle />
            <ResizablePanel defaultSize={isSandboxPanel ? 65 : 50} minSize={25}>
              {panelContent}
            </ResizablePanel>
          </>
        )}
      </ResizablePanelGroup>
      {upgradeDialog}
    </>
  )
}
