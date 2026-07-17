'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createApiClient, archiveMemory, listMemory } from '@cubeplex/core'
import type { MemoryItem, MemoryScope, MemoryStatus } from '@cubeplex/core'
import { useTranslations } from 'next-intl'
import { Brain } from 'lucide-react'
import { EmptyState } from '@/components/shared/EmptyState'
import { MemoryItemCard } from './MemoryItemCard'

interface MemoryListProps {
  wsId: string
  scope?: MemoryScope
  status?: MemoryStatus
  sourceConversationId?: string
}

export function MemoryList({
  wsId,
  scope,
  status = 'active',
  sourceConversationId,
}: MemoryListProps) {
  const t = useTranslations('wsSettings.memory')
  const [items, setItems] = useState<MemoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const loadingRef = useRef(false)

  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    let cancelled = false
    loadingRef.current = true
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true)
    setError(null)
    listMemory(client, { scope, status, source_conversation_id: sourceConversationId })
      .then((data) => {
        if (!cancelled) setItems(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load memories')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
        loadingRef.current = false
      })
    return () => {
      cancelled = true
    }
  }, [client, scope, status, sourceConversationId])

  const handleArchive = useCallback(
    async (id: string) => {
      await archiveMemory(client, id)
      setItems((prev) => prev.filter((item) => item.id !== id))
    },
    [client],
  )

  if (loading) {
    return (
      <div className="flex flex-col gap-3">
        {[...Array(3)].map((_, i) => (
          <div key={i} className="h-24 rounded-xl border border-border bg-muted/30 animate-pulse" />
        ))}
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
        {error}
      </div>
    )
  }

  if (items.length === 0) {
    return <EmptyState icon={Brain} title={t('emptyTitle')} description={t('emptyHint')} />
  }

  return (
    <div className="flex flex-col gap-3">
      {items.map((item) => (
        <MemoryItemCard key={item.id} item={item} onArchive={handleArchive} />
      ))}
    </div>
  )
}
