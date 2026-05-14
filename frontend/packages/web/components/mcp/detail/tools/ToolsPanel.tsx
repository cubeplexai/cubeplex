'use client'

import type { MCPToolEntry } from '@cubebox/core'
import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'

import { ToolDetail, type ToolDetailView } from './ToolDetail'
import { ToolList } from './ToolList'

export interface ToolsPanelProps {
  tools: MCPToolEntry[]
}

export function ToolsPanel({ tools }: ToolsPanelProps) {
  const t = useTranslations('mcp.tools')
  const [selectedName, setSelectedName] = useState<string | null>(
    tools.length > 0 ? tools[0].name : null,
  )
  const [view, setView] = useState<ToolDetailView>('schema')

  useEffect(() => {
    if (tools.length === 0) {
      setSelectedName(null)
      return
    }
    if (!selectedName || !tools.some((tool) => tool.name === selectedName)) {
      setSelectedName(tools[0].name)
    }
  }, [tools, selectedName])

  const selected = tools.find((tool) => tool.name === selectedName) ?? null

  if (tools.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border px-6 py-12 text-center text-sm text-muted-foreground">
        {t('empty')}
      </div>
    )
  }

  return (
    <div className="grid min-h-[420px] grid-cols-[280px_minmax(0,1fr)] gap-6">
      <aside className="min-h-0 border-r border-border/60 pr-4">
        <ToolList tools={tools} selectedName={selectedName} onSelect={setSelectedName} />
      </aside>
      <section className="min-h-0">
        {selected ? <ToolDetail tool={selected} view={view} onViewChange={setView} /> : null}
      </section>
    </div>
  )
}
