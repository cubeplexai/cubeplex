'use client'

export function McpPanel({ wsId }: { wsId: string }): React.ReactElement {
  return (
    <div className="flex-1 p-8">
      <p className="text-muted-foreground">MCP panel — coming soon. wsId: {wsId}</p>
    </div>
  )
}
