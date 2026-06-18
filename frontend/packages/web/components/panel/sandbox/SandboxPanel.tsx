'use client'

import { useState, useCallback, useRef } from 'react'
import { FolderOpen, Globe, Loader2, TerminalSquare, RefreshCw, X } from 'lucide-react'
import { usePanelStore } from '@cubebox/core'
import { useSWRConfig } from 'swr'

import { BrowserView } from '@/components/panel/BrowserView'
import { SandboxFilesView } from './SandboxFilesView'
import { SandboxTerminalView } from './SandboxTerminalView'
import { cn } from '@/lib/utils'

type SandboxTab = 'files' | 'browser' | 'terminal'

interface SandboxPanelProps {
  workspaceId: string | null
  conversationId?: string | null
}

const TABS: {
  id: SandboxTab
  label: string
  Icon: typeof FolderOpen
}[] = [
  { id: 'files', label: 'Files', Icon: FolderOpen },
  { id: 'browser', label: 'Browser', Icon: Globe },
  { id: 'terminal', label: 'Terminal', Icon: TerminalSquare },
]

export function SandboxPanel({ workspaceId, conversationId }: SandboxPanelProps) {
  const [activeTab, setActiveTab] = useState<SandboxTab>('files')
  const [refreshing, setRefreshing] = useState(false)
  const close = usePanelStore((s) => s.close)
  const { mutate } = useSWRConfig()
  const browserRefreshRef = useRef<(() => void) | null>(null)
  const terminalRefreshRef = useRef<(() => Promise<unknown>) | null>(null)

  const handleRefresh = useCallback(async () => {
    setRefreshing(true)
    try {
      if (activeTab === 'files') {
        await mutate((key: unknown) => typeof key === 'string' && key.includes('/sandbox/files'))
      } else if (activeTab === 'browser') {
        browserRefreshRef.current?.()
        await new Promise((r) => setTimeout(r, 400))
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
          <SandboxFilesView workspaceId={workspaceId} conversationId={conversationId} />
        )}
        {activeTab === 'browser' && (
          <BrowserView
            workspaceId={workspaceId}
            conversationId={conversationId}
            hideHeader
            refreshRef={browserRefreshRef}
          />
        )}
        {activeTab === 'terminal' && (
          <SandboxTerminalView
            workspaceId={workspaceId}
            conversationId={conversationId}
            refreshRef={terminalRefreshRef}
          />
        )}
      </div>
    </div>
  )
}
