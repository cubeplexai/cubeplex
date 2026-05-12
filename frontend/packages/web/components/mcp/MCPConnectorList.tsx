'use client'

import { useTranslations } from 'next-intl'
import type { MCPAdminConnector, MCPConnectorFilter } from '@cubebox/core'
import { MCPConnectorCard } from './MCPConnectorCard'

interface MCPConnectorListProps {
  connectors: MCPAdminConnector[]
  loading: boolean
  search: string
  filter: MCPConnectorFilter
  selectedId: string | null
  onSelect: (id: string) => void
}

function filterConnectors(
  connectors: MCPAdminConnector[],
  search: string,
  filter: MCPConnectorFilter,
): MCPAdminConnector[] {
  const q = search.trim().toLowerCase()

  return connectors
    .filter((c) => {
      if (filter === 'installed' && !c.installed) return false
      if (filter === 'available' && (c.installed || c.kind !== 'catalog')) return false
      if (filter === 'custom' && c.kind !== 'custom') return false
      if (q) {
        const haystack = `${c.name} ${c.provider} ${c.description}`.toLowerCase()
        if (!haystack.includes(q)) return false
      }
      return true
    })
    .sort((a, b) => {
      // Installed connectors first
      if (a.installed !== b.installed) return a.installed ? -1 : 1
      // Then alphabetical by name
      return a.name.localeCompare(b.name)
    })
}

export function MCPConnectorList({
  connectors,
  loading,
  search,
  filter,
  selectedId,
  onSelect,
}: MCPConnectorListProps) {
  const t = useTranslations('mcpAdmin')

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center py-10 text-sm text-muted-foreground">
        {t('loading')}
      </div>
    )
  }

  const filtered = filterConnectors(connectors, search, filter)

  if (filtered.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-1 py-10 text-center">
        <p className="text-sm font-medium text-foreground">{t('noConnectors')}</p>
        <p className="text-xs text-muted-foreground">{t('noConnectorsHint')}</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-1.5 p-3">
      {filtered.map((connector) => (
        <MCPConnectorCard
          key={connector.id}
          connector={connector}
          active={connector.id === selectedId}
          onClick={() => onSelect(connector.id)}
        />
      ))}
    </div>
  )
}
