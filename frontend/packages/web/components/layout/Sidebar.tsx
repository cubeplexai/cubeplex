'use client'

import { useConversationStore, createApiClient } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import Link from 'next/link'
import { Plus, Trash2 } from 'lucide-react'

export function Sidebar() {
  const { conversations, activeId, remove, setActive } = useConversationStore()

  const handleDeleteClick = async (e: React.MouseEvent, id: string) => {
    e.preventDefault()
    const client = createApiClient('')
    try {
      await remove(client, id)
    } catch (err) {
      console.error('Failed to delete conversation:', err)
    }
  }

  return (
    <div className="w-64 bg-card border-r border-border flex flex-col h-screen">
      <div className="p-4 border-b border-border">
        <Button className="w-full" size="sm">
          <Plus className="size-4" />
          新建对话
        </Button>
      </div>
      <ScrollArea className="flex-1">
        <div className="p-2 space-y-1">
          {conversations.map((convo) => (
            <Link
              key={convo.id}
              href={`/conversations/${convo.id}`}
              onClick={() => setActive(convo.id)}
              className={`block p-3 rounded-lg text-sm transition-colors truncate ${ activeId === convo.id ? 'bg-primary/10 text-primary' : 'hover:bg-accent/30 text-muted-foreground'}`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="flex-1 truncate">{convo.title || '新对话'}</span>
                <button
                  onClick={(e) => handleDeleteClick(e, convo.id)}
                  className="opacity-0 group-hover:opacity-100 transition-opacity"
                >
                  <Trash2 className="size-3" />
                </button>
              </div>
            </Link>
          ))}
        </div>
      </ScrollArea>
    </div>
  )
}
