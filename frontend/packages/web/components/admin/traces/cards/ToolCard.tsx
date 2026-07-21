'use client'

import { Wrench } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import type { ToolCallPayload } from '../types'
import { JsonBlock } from './JsonBlock'
import { Section } from './Section'

interface Props {
  tool: ToolCallPayload
}

export function ToolCard({ tool }: Props) {
  const t = useTranslations('adminTraces.sections')
  return (
    <div className="space-y-3">
      <Card className="flex-row items-start gap-3 p-4">
        <div className="rounded-md bg-success-surface p-2 text-success-fg">
          <Wrench className="size-4" />
        </div>
        <div className="flex-1 space-y-1">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-semibold">{tool.name}</span>
            {tool.is_error && <Badge variant="destructive">error</Badge>}
          </div>
          {tool.description && <p className="text-xs text-muted-foreground">{tool.description}</p>}
          {tool.execution_mode && (
            <p className="text-[11px] text-muted-foreground">mode: {tool.execution_mode}</p>
          )}
        </div>
      </Card>
      <Section title={t('arguments')}>
        <JsonBlock value={tool.arguments} />
      </Section>
      <Section title={t('result')}>
        <JsonBlock value={tool.result} language="text" />
      </Section>
    </div>
  )
}
