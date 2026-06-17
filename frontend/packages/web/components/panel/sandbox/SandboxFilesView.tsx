'use client'

import { useState } from 'react'
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable'
import { SandboxFileTree } from './SandboxFileTree'
import { SandboxFilePreview } from './SandboxFilePreview'
import type { SandboxFileEntry } from '@/hooks/useSandboxFiles'

interface SandboxFilesViewProps {
  workspaceId: string
  conversationId?: string | null
}

export function SandboxFilesView({ workspaceId, conversationId }: SandboxFilesViewProps) {
  const [selectedFile, setSelectedFile] = useState<SandboxFileEntry | null>(null)

  return (
    <ResizablePanelGroup orientation="horizontal" className="h-full">
      <ResizablePanel defaultSize={selectedFile ? 30 : 100} minSize={20}>
        <SandboxFileTree
          workspaceId={workspaceId}
          conversationId={conversationId}
          onSelectFile={setSelectedFile}
          selectedPath={selectedFile?.path ?? null}
        />
      </ResizablePanel>
      {selectedFile && (
        <>
          <ResizableHandle withHandle />
          <ResizablePanel defaultSize={70} minSize={30}>
            <SandboxFilePreview
              key={selectedFile.path}
              entry={selectedFile}
              workspaceId={workspaceId}
              conversationId={conversationId}
            />
          </ResizablePanel>
        </>
      )}
    </ResizablePanelGroup>
  )
}
