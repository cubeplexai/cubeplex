'use client'

import { useConversationStore, createApiClient } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import Link from 'next/link'
import { Plus, Trash2, Box } from 'lucide-react'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'

function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)

  if (diffMins < 1) return '刚刚'
  if (diffMins < 60) return `${diffMins}m 前`
  if (diffHours < 24) return `${diffHours}h 前`
  return `${diffDays}d 前`
}

export function Sidebar() {
  const { conversations, activeId, remove, setActive } = useConversationStore()
  const { workspaceId } = useWorkspaceContext()
  const homeHref = workspaceId ? `/w/${workspaceId}` : '/'

  const handleDeleteClick = async (e: React.MouseEvent, id: string) => {
    e.preventDefault()
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    try {
      await remove(client, id)
    } catch (err) {
      console.error('Failed to delete conversation:', err)
    }
  }

  return (
    <div className="w-56 bg-card border-r border-border flex flex-col h-screen shrink-0">
      {/* Brand */}
      <div className="px-4 pt-4 pb-3 border-b border-border">
        <div className="flex items-center gap-2 mb-3">
          <div className="w-6 h-6 rounded-md bg-primary flex items-center justify-center shrink-0">
            <Box className="size-3.5 text-white" strokeWidth={2.5} />
          </div>
          <span className="text-sm font-semibold tracking-tight">cubebox</span>
        </div>
        <Link href={homeHref}>
          <Button variant="outline" size="sm" className="w-full h-7 text-xs gap-1.5">
            <Plus className="size-3" />
            新建对话
          </Button>
        </Link>
      </div>

      {/* Conversation list */}
      <ScrollArea className="flex-1">
        <div className="py-2 px-2">
          <div className="px-2 py-1 text-[10px] font-medium uppercase tracking-widest text-muted-foreground/50 mb-1">
            历史对话
          </div>
          {conversations.map((convo) => (
            <Link
              key={convo.id}
              href={
                workspaceId
                  ? `/w/${workspaceId}/conversations/${convo.id}`
                  : `/conversations/${convo.id}`
              }
              onClick={() => setActive(convo.id)}
              className={`group relative flex items-center gap-2 px-2 py-2 rounded-md transition-colors ${
                activeId === convo.id
                  ? 'text-foreground bg-primary/8'
                  : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
              }`}
            >
              {activeId === convo.id && (
                <div className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 bg-primary rounded-r-full" />
              )}
              <div className="flex-1 min-w-0 pl-1">
                <div className="truncate text-[12.5px] font-medium leading-none mb-1">
                  {convo.title || '新对话'}
                </div>
                <div className="text-[10px] text-muted-foreground/50">
                  {formatRelativeTime(convo.created_at)}
                </div>
              </div>
              <button
                onClick={(e) => handleDeleteClick(e, convo.id)}
                className="opacity-0 group-hover:opacity-40 hover:!opacity-80 transition-opacity shrink-0 p-0.5"
              >
                <Trash2 className="size-3" />
              </button>
            </Link>
          ))}
        </div>
      </ScrollArea>
    </div>
  )
}
