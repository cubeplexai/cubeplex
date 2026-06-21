'use client'

import { useCallback, useEffect, useState } from 'react'
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable'
import { SandboxFileTree } from './SandboxFileTree'
import { SandboxFilePreview } from './SandboxFilePreview'
import type { SandboxFileEntry } from '@/hooks/useSandboxFiles'

interface SandboxFilesViewProps {
  workspaceId: string
  conversationId?: string | null
  initialFilePath?: string | null
  initialFilePathRevision?: number | null
}

function makeStubEntry(path: string): SandboxFileEntry {
  return {
    path,
    name: path.slice(path.lastIndexOf('/') + 1),
    is_dir: false,
    size: 0,
    modified_at: '',
  }
}

export function SandboxFilesView({
  workspaceId,
  conversationId,
  initialFilePath,
  initialFilePathRevision,
}: SandboxFilesViewProps) {
  const [selectedFile, setSelectedFile] = useState<SandboxFileEntry | null>(() =>
    initialFilePath ? makeStubEntry(initialFilePath) : null,
  )

  // Re-select whenever the panel store hands us a fresh navigation target
  // (revision changes on every openSandboxFile call, even with same path).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- syncing from external panel store
    if (initialFilePath) setSelectedFile(makeStubEntry(initialFilePath))
  }, [initialFilePath, initialFilePathRevision])

  const handleNavigate = useCallback((path: string) => {
    setSelectedFile(makeStubEntry(path))
  }, [])

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
              onNavigate={handleNavigate}
            />
          </ResizablePanel>
        </>
      )}
    </ResizablePanelGroup>
  )
}
