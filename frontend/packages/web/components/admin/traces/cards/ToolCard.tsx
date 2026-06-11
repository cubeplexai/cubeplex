'use client'

import { useTranslations } from 'next-intl'

import type { ToolCallPayload } from '../types'
import { JsonBlock } from './JsonBlock'

interface Props {
  tool: ToolCallPayload
}

export function ToolCard({ tool }: Props) {
  const t = useTranslations('adminTraces.sections')
  return (
    <div className="space-y-3">
      <div className="rounded border border-border bg-card p-3">
        <div className="text-xs text-muted-foreground">{t('toolInfo')}</div>
        <div className="mt-1 font-mono text-sm font-semibold">{tool.name}</div>
        {tool.description && (
          <div className="mt-1 text-xs text-muted-foreground">{tool.description}</div>
        )}
        {tool.is_error && <div className="mt-2 text-xs font-medium text-destructive">errored</div>}
      </div>
      <div>
        <div className="mb-1 text-xs font-medium text-muted-foreground">{t('arguments')}</div>
        <JsonBlock value={tool.arguments} />
      </div>
      <div>
        <div className="mb-1 text-xs font-medium text-muted-foreground">{t('result')}</div>
        <JsonBlock value={tool.result} language="text" />
      </div>
    </div>
  )
}
