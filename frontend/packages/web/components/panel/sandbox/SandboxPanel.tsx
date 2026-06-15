'use client'

import { useState } from 'react'
import { FolderOpen, Globe, TerminalSquare } from 'lucide-react'
import { usePanelStore } from '@cubebox/core'

import { PanelHeader } from '@/components/panel/PanelHeader'
import { BrowserView } from '@/components/panel/BrowserView'
import { SandboxFilesView } from './SandboxFilesView'
import { SandboxTerminalView } from './SandboxTerminalView'
import { cn } from '@/lib/utils'

type SandboxTab = 'files' | 'browser' | 'terminal'

interface SandboxPanelProps {
  workspaceId: string | null
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

export function SandboxPanel({ workspaceId }: SandboxPanelProps) {
  const [activeTab, setActiveTab] = useState<SandboxTab>('files')
  const close = usePanelStore((s) => s.close)

  if (!workspaceId) return null

  return (
    <div className="flex h-full w-full flex-col">
      <PanelHeader
        source={{
          kind: 'plain',
          icon: null,
          title: 'Sandbox',
        }}
        onClose={close}
      />
      <div
        className={cn(
          'flex border-b border-border bg-card shrink-0',
        )}
      >
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
      </div>
      <div className="flex-1 overflow-hidden">
        {activeTab === 'files' && (
          <SandboxFilesView workspaceId={workspaceId} />
        )}
        {activeTab === 'browser' && (
          <BrowserView workspaceId={workspaceId} />
        )}
        {activeTab === 'terminal' && (
          <SandboxTerminalView workspaceId={workspaceId} />
        )}
      </div>
    </div>
  )
}
