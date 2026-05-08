'use client'

import { useState } from 'react'
import type { MCPCatalogConnector } from '@cubebox/core'
import { Globe } from 'lucide-react'

import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'

import { deriveCatalogStatus, StatusChip } from './StatusChip'

export interface MCPCatalogCardProps {
  connector: MCPCatalogConnector
  mode: 'admin' | 'workspace'
  onSelect: (connector: MCPCatalogConnector) => void
}

function readIconUrl(metadata: Record<string, unknown>): string | null {
  const raw = metadata.icon_url
  if (typeof raw === 'string' && raw.length > 0) return raw
  return null
}

export function MCPCatalogCard({ connector, mode, onSelect }: MCPCatalogCardProps) {
  const status = deriveCatalogStatus(connector, mode)
  const iconUrl = readIconUrl(connector.metadata)
  const [imgFailed, setImgFailed] = useState(false)
  const showImage = iconUrl !== null && !imgFailed

  return (
    <Card
      role="button"
      tabIndex={0}
      onClick={() => onSelect(connector)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onSelect(connector)
        }
      }}
      className={cn(
        'flex cursor-pointer flex-col gap-3 p-5 transition-colors',
        'hover:border-primary/40 hover:shadow-md',
        'focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50',
      )}
      aria-label={`${connector.name} (${connector.provider})`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="flex size-10 items-center justify-center overflow-hidden rounded-md border border-border bg-muted/40">
            {showImage ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={iconUrl}
                alt=""
                width={32}
                height={32}
                className="size-8 object-contain"
                onError={() => setImgFailed(true)}
              />
            ) : (
              <Globe className="size-5 text-muted-foreground" aria-hidden />
            )}
          </div>
          <div className="flex flex-col">
            <span className="font-medium leading-tight">{connector.name}</span>
            <span className="text-xs text-muted-foreground">{connector.provider}</span>
          </div>
        </div>
        <StatusChip status={status} />
      </div>
      <p className="line-clamp-3 text-sm text-muted-foreground">{connector.description}</p>
    </Card>
  )
}
