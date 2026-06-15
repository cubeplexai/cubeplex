'use client'

import { useState, useCallback } from 'react'
import { ChevronRight, Download, Loader2, FolderOpen, Folder } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useSandboxFiles, type SandboxFileEntry } from '@/hooks/useSandboxFiles'
import { getFileVisual } from '@/lib/fileIcons'

interface SandboxFileTreeProps {
  workspaceId: string
  onSelectFile: (entry: SandboxFileEntry) => void
  selectedPath: string | null
}

export function SandboxFileTree({ workspaceId, onSelectFile, selectedPath }: SandboxFileTreeProps) {
  return (
    <div className="h-full overflow-auto py-1">
      <TreeDirectory
        workspaceId={workspaceId}
        path="/workspace"
        depth={0}
        defaultOpen
        onSelectFile={onSelectFile}
        selectedPath={selectedPath}
      />
    </div>
  )
}

function TreeDirectory({
  workspaceId,
  path,
  depth,
  defaultOpen = false,
  onSelectFile,
  selectedPath,
}: {
  workspaceId: string
  path: string
  depth: number
  defaultOpen?: boolean
  onSelectFile: (entry: SandboxFileEntry) => void
  selectedPath: string | null
}) {
  const [open, setOpen] = useState(defaultOpen)
  const { files, loading } = useSandboxFiles(open ? workspaceId : null, path)

  return (
    <>
      {depth > 0 && (
        <TreeRow
          name={path.split('/').pop() || path}
          isDir
          depth={depth}
          open={open}
          onClick={() => setOpen((v) => !v)}
          selected={false}
          workspaceId={workspaceId}
          path={path}
        />
      )}
      {open && loading && (
        <div
          className={'flex items-center gap-1.5 py-1' + ' text-xs text-muted-foreground'}
          style={{ paddingLeft: (depth + 1) * 16 + 8 }}
        >
          <Loader2 className="size-3 animate-spin" />
          Loading…
        </div>
      )}
      {open &&
        files.map((entry) =>
          entry.is_dir ? (
            <TreeDirectory
              key={entry.path}
              workspaceId={workspaceId}
              path={entry.path}
              depth={depth + 1}
              onSelectFile={onSelectFile}
              selectedPath={selectedPath}
            />
          ) : (
            <TreeRow
              key={entry.path}
              name={entry.name}
              isDir={false}
              depth={depth + 1}
              selected={selectedPath === entry.path}
              onClick={() => onSelectFile(entry)}
              workspaceId={workspaceId}
              path={entry.path}
            />
          ),
        )}
    </>
  )
}

function TreeRow({
  name,
  isDir,
  depth,
  open,
  selected,
  onClick,
  workspaceId,
  path,
}: {
  name: string
  isDir: boolean
  depth: number
  open?: boolean
  selected: boolean
  onClick: () => void
  workspaceId: string
  path: string
}) {
  const visual = isDir ? null : getFileVisual({ filename: name })
  const FileIcon = visual?.Icon

  const handleDownload = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation()
      const ep = encodeURIComponent(path)
      const url = `/api/v1/ws/${workspaceId}/sandbox/files/download` + `?path=${ep}`
      window.open(url, '_blank')
    },
    [workspaceId, path],
  )

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'group flex w-full items-center gap-1',
        'py-1 pr-2 text-xs hover:bg-muted/50',
        'transition-colors text-left',
        selected && 'bg-muted',
      )}
      style={{ paddingLeft: depth * 16 + 8 }}
    >
      {isDir ? (
        <>
          <ChevronRight
            className={cn(
              'size-3 shrink-0 text-muted-foreground',
              'transition-transform',
              open && 'rotate-90',
            )}
          />
          {open ? (
            <FolderOpen className="size-3.5 shrink-0 text-warning-solid" />
          ) : (
            <Folder className="size-3.5 shrink-0 text-warning-solid" />
          )}
        </>
      ) : (
        <>
          <span className="size-3 shrink-0" />
          {FileIcon ? (
            <FileIcon className="size-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <span className="size-3.5 shrink-0" />
          )}
        </>
      )}
      <span className="flex-1 truncate">{name}</span>
      {!isDir && (
        <span
          role="button"
          tabIndex={-1}
          onClick={handleDownload}
          className={'hidden group-hover:block p-0.5' + ' rounded hover:bg-accent'}
          title="Download"
        >
          <Download className="size-3 text-muted-foreground" />
        </span>
      )}
    </button>
  )
}
