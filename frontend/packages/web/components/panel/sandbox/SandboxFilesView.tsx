'use client'

interface SandboxFilesViewProps {
  workspaceId: string
}

export function SandboxFilesView({ workspaceId: _workspaceId }: SandboxFilesViewProps) {
  return (
    <div
      className="flex h-full items-center justify-center
        text-sm text-muted-foreground"
    >
      File browser — coming next
    </div>
  )
}
