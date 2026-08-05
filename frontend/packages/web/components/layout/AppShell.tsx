'use client'

import { ReactNode, useEffect, useRef, useState } from 'react'
import { FolderOpen, Layers, Menu, TerminalSquare, UserPlus, X } from 'lucide-react'
import { SiGooglechrome } from 'react-icons/si'
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
import { useConversationStore, usePanelStore, type SandboxTab } from '@cubeplex/core'
import { useDeploymentMode } from '@cubeplex/core/hooks/useDeploymentMode'
import { SharePanel } from '@/components/chat/SharePanel'
import { ConversationMemberStrip } from '@/components/chat/ConversationMemberStrip'
import { CreateGroupChatDialog } from '@/components/dialogs/CreateGroupChatDialog'
import { UpgradeToTopicDialog } from '@/components/dialogs/UpgradeToTopicDialog'
import { InviteToConversationDialog } from '@/components/dialogs/InviteToConversationDialog'

const SANDBOX_HEADER_ACTIONS: {
  tab: SandboxTab
  label: string
  testId: string
  // Lucide icons + monochrome SiGooglechrome (currentColor, no brand blue fill).
  Icon: typeof FolderOpen | typeof SiGooglechrome
}[] = [
  { tab: 'files', label: 'Files', testId: 'header-sandbox-files', Icon: FolderOpen },
  { tab: 'browser', label: 'Browser', testId: 'header-sandbox-browser', Icon: SiGooglechrome },
  { tab: 'terminal', label: 'Terminal', testId: 'header-sandbox-terminal', Icon: TerminalSquare },
]

interface AppShellProps {
  children: ReactNode
  headerTitle?: string
  conversationId?: string
  /**
   * `full` — conversation chrome (title, invite, share, topic, sandbox, theme).
   * `minimal` — new-chat home: topic + sandbox + theme only.
   */
  headerVariant?: 'full' | 'minimal'
}

export function AppShell({
  children,
  headerTitle,
  conversationId,
  headerVariant = 'full',
}: AppShellProps) {
  const tTopics = useTranslations('topics')
  const tUpgrade = useTranslations('topics.upgradeDialog')
  const tInvite = useTranslations('conversation.invite')
  const view = usePanelStore((s) => s.view)
  const openSandbox = usePanelStore((s) => s.openSandbox)
  const { workspaceId } = useWorkspaceContext()
  const conversation = useConversationStore((s) =>
    conversationId ? s.conversations.find((c) => c.id === conversationId) : undefined,
  )
  const canUpgrade = Boolean(workspaceId && conversation && !conversation.topic_id)
  const minimal = headerVariant === 'minimal'
  const [upgradeOpen, setUpgradeOpen] = useState(false)
  const [createTopicOpen, setCreateTopicOpen] = useState(false)
  const [inviteOpen, setInviteOpen] = useState(false)
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
      <SandboxPanel workspaceId={workspaceId} conversationId={conversationId} />
    ) : view.type === 'skill-candidate' ? (
      <SkillCandidatePanel
        candidateId={view.candidateId}
        repo={view.repo}
        sourceName={view.sourceName}
      />
    ) : (
      <ToolDetailPanel />
    )

  // Conversation pages only: promote an existing chat into a topic.
  const upgradeDialog = !minimal && workspaceId && conversation && !conversation.topic_id && (
    <UpgradeToTopicDialog
      wsId={workspaceId}
      conversationId={conversation.id}
      initialTitle={conversation.title ?? ''}
      open={upgradeOpen}
      onOpenChange={setUpgradeOpen}
    />
  )

  // New-chat home only: same create-topic flow as the sidebar "New Topic" button.
  // Mount only while open so unit tests / cold paths don't pull auth/member stores.
  const createTopicDialog = minimal && workspaceId && createTopicOpen && (
    <CreateGroupChatDialog
      wsId={workspaceId}
      open={createTopicOpen}
      onOpenChange={setCreateTopicOpen}
    />
  )

  const inviteDialog = workspaceId && conversation && (
    <InviteToConversationDialog
      wsId={workspaceId}
      conversationId={conversation.id}
      open={inviteOpen}
      onOpenChange={setInviteOpen}
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
        <span className="text-sm text-muted-foreground truncate flex-1">
          {minimal ? '' : headerTitle || ''}
        </span>
        {!minimal && workspaceId && conversation?.is_group_chat && (
          <ConversationMemberStrip wsId={workspaceId} conversationId={conversation.id} />
        )}
        {/* Topic control:
            - minimal (new chat): create a topic — same as sidebar New Topic
            - full (conversation): promote this chat to a topic when eligible */}
        {minimal && workspaceId && (
          <button
            type="button"
            onClick={() => setCreateTopicOpen(true)}
            className="mr-1 cursor-pointer rounded p-1.5 text-muted-foreground hover:bg-accent transition-colors duration-fast"
            aria-label={tTopics('newTopic')}
            title={tTopics('newTopic')}
            data-testid="header-topic-button"
          >
            <Layers className="h-4 w-4" />
          </button>
        )}
        {!minimal && canUpgrade && (
          <button
            type="button"
            onClick={() => setUpgradeOpen(true)}
            className="mr-1 cursor-pointer rounded p-1.5 text-muted-foreground hover:bg-accent transition-colors duration-fast"
            aria-label={tUpgrade('promoteLabel')}
            title={tUpgrade('promoteLabel')}
            data-testid="header-topic-button"
          >
            <Layers className="h-4 w-4" />
          </button>
        )}
        {!minimal && workspaceId && conversation && (
          <button
            type="button"
            onClick={() => setInviteOpen(true)}
            className="mr-1 rounded p-1.5 text-muted-foreground hover:bg-accent transition-colors duration-fast"
            aria-label={tInvite('button')}
            title={tInvite('button')}
            data-testid="conversation-invite-button"
          >
            <UserPlus className="h-4 w-4" />
          </button>
        )}
        {!minimal && conversationId && <SharePanel conversationId={conversationId} />}
        {workspaceId && sandboxEnabled && (
          <div className="mr-1 flex items-center gap-0.5">
            {SANDBOX_HEADER_ACTIONS.map(({ tab, label, testId, Icon }) => (
              <button
                key={tab}
                type="button"
                onClick={() => openSandbox(tab)}
                className="cursor-pointer rounded p-1.5 text-muted-foreground hover:bg-accent transition-colors duration-fast"
                aria-label={label}
                title={label}
                data-testid={testId}
              >
                <Icon className="h-4 w-4" />
              </button>
            ))}
          </div>
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
        {createTopicDialog}
        {inviteDialog}
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
          // Percent of the main content area (sidebar is outside this group).
          // Sandbox defaults give chat ~40% so a wider nav rail still leaves a
          // usable conversation column; the handle can drag either way. Other
          // panels (artifact/tool) stay 50/50.
          defaultSize={panelOpen ? (isSandboxPanel ? 40 : 50) : 100}
          minSize={isSandboxPanel ? 28 : 30}
        >
          {main}
        </ResizablePanel>

        {panelOpen && (
          <>
            <ResizableHandle withHandle />
            <ResizablePanel
              defaultSize={isSandboxPanel ? 60 : 50}
              minSize={isSandboxPanel ? 30 : 25}
            >
              {panelContent}
            </ResizablePanel>
          </>
        )}
      </ResizablePanelGroup>
      {upgradeDialog}
      {createTopicDialog}
      {inviteDialog}
    </>
  )
}
