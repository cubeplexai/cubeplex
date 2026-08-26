'use client'

import { useState, useCallback, useEffect, useRef, type ComponentType } from 'react'
import { FolderOpen, Loader2, TerminalSquare, RefreshCw, X } from 'lucide-react'
import { SiGooglechrome } from 'react-icons/si'
import { usePanelStore, type SandboxTab } from '@cubeplex/core'
import { useSWRConfig } from 'swr'

import { BrowserView } from '@/components/panel/BrowserView'
import { SandboxFilesView } from './SandboxFilesView'
import { SandboxTerminalView } from './SandboxTerminalView'
import { cn } from '@/lib/utils'

interface SandboxPanelProps {
  workspaceId: string | null
  conversationId?: string | null
}

const TABS: {
  id: SandboxTab
  label: string
  Icon: ComponentType<{ className?: string }>
}[] = [
  { id: 'files', label: 'Files', Icon: FolderOpen },
  // Match AppShell header: monochrome Chrome (currentColor), not Lucide Globe.
  { id: 'browser', label: 'Browser', Icon: SiGooglechrome },
  { id: 'terminal', label: 'Terminal', Icon: TerminalSquare },
]

/** Last workspace/conversation the sandbox panel rendered for. Module-level so
 *  remounts (route changes) can detect a scope change if a path leaves the
 *  panel open. */
let lastSandboxScopeKey: string | null = null

function sandboxScopeKey(workspaceId: string | null, conversationId?: string | null): string {
  return `${workspaceId ?? ''}:${conversationId ?? ''}`
}

export function SandboxPanel({ workspaceId, conversationId }: SandboxPanelProps) {
  const [activeTab, setActiveTab] = useState<SandboxTab>('files')
  const [refreshing, setRefreshing] = useState(false)
  const close = usePanelStore((s) => s.close)
  const openSandbox = usePanelStore((s) => s.openSandbox)
  const sandboxView = usePanelStore((s) => (s.view.type === 'sandbox' ? s.view : null))
  const initialFilePath = sandboxView?.initialFilePath ?? null
  const initialTab = sandboxView?.initialTab
  const sandboxRevision = sandboxView?.revision ?? null
  const { mutate } = useSWRConfig()
  const browserRefreshRef = useRef<(() => Promise<void>) | null>(null)
  const terminalRefreshRef = useRef<(() => Promise<unknown>) | null>(null)
  const scopeKey = sandboxScopeKey(workspaceId, conversationId)

  // If the user navigates while the global sandbox panel stays open (a path
  // that does not call panelStore.close), drop any file path targeted for the
  // previous conversation so we don't fetch it under the wrong sandbox scope.
  useEffect(() => {
    const prev = lastSandboxScopeKey
    lastSandboxScopeKey = scopeKey
    if (prev === null || prev === scopeKey) return
    const view = usePanelStore.getState().view
    if (view.type !== 'sandbox') return
    if (!view.initialFilePath) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- store sync on route scope change
    openSandbox(view.initialTab ?? 'files')
  }, [scopeKey, openSandbox])

  // When the panel is closed (conversation switch calls close()), forget the
  // scope so the next openSandboxFile on a new conversation is not treated as
  // a cross-scope leak and wiped on mount.
  useEffect(() => {
    return () => {
      if (usePanelStore.getState().view.type !== 'sandbox') {
        lastSandboxScopeKey = null
      }
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- syncing from external panel store
    if (initialFilePath) setActiveTab('files')
    else if (initialTab) setActiveTab(initialTab)
  }, [initialFilePath, initialTab, sandboxRevision])

  const handleRefresh = useCallback(async () => {
    setRefreshing(true)
    try {
      if (activeTab === 'files') {
        await mutate((key: unknown) => typeof key === 'string' && key.includes('/sandbox/files'))
      } else if (activeTab === 'browser') {
        await browserRefreshRef.current?.()
      } else if (activeTab === 'terminal') {
        await terminalRefreshRef.current?.()
      }
    } finally {
      setRefreshing(false)
    }
  }, [activeTab, mutate])

  if (!workspaceId) return null

  return (
    <div className="flex h-full w-full flex-col">
      <div className="flex h-11 items-center border-b border-border bg-card shrink-0">
        {TABS.map(({ id, label, Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => setActiveTab(id)}
            className={cn(
              'flex items-center gap-1.5 px-4 py-2',
              'text-xs font-medium transition-colors',
              activeTab === id
                ? 'text-foreground border-b-2 border-primary'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            <Icon className="size-3.5" />
            {label}
          </button>
        ))}
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => void handleRefresh()}
          disabled={refreshing}
          className={cn('p-1.5 text-muted-foreground hover:text-foreground', 'transition-colors')}
          title="Refresh"
        >
          <RefreshCw className={cn('size-3.5', refreshing && 'animate-spin')} />
        </button>
        <button
          type="button"
          onClick={close}
          className={cn(
            'p-1.5 mr-1 text-muted-foreground hover:text-foreground',
            'transition-colors',
          )}
          title="Close"
        >
          <X className="size-3.5" />
        </button>
      </div>
      <div className="relative flex-1 overflow-hidden">
        {refreshing && (
          <div className="absolute inset-0 z-20 grid place-items-center bg-background/50">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        )}
        {activeTab === 'files' && (
          <SandboxFilesView
            // Remount tree/preview when scope changes so local selectedFile
            // cannot leak across conversations.
            key={scopeKey}
            workspaceId={workspaceId}
            conversationId={conversationId}
            initialFilePath={initialFilePath}
            initialFilePathRevision={sandboxRevision}
          />
        )}
        {activeTab === 'browser' && (
          <BrowserView
            key={scopeKey}
            workspaceId={workspaceId}
            conversationId={conversationId}
            hideHeader
            refreshRef={browserRefreshRef}
          />
        )}
        {activeTab === 'terminal' && (
          <SandboxTerminalView
            key={scopeKey}
            workspaceId={workspaceId}
            conversationId={conversationId}
            refreshRef={terminalRefreshRef}
          />
        )}
      </div>
    </div>
  )
}
