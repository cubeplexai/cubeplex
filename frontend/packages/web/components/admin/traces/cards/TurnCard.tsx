'use client'

import { MessageSquare, RotateCw } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import type { TurnPayload } from '../types'
import { MessageList } from './MessageList'
import { Section } from './Section'

interface Props {
  turn: TurnPayload
}

export function TurnCard({ turn }: Props) {
  const t = useTranslations('adminTraces.sections')
  return (
    <div className="space-y-3">
      <Card className="flex-row items-center gap-3 p-4">
        <div className="rounded-md bg-warning-surface p-2 text-warning-fg">
          <RotateCw className="size-4" />
        </div>
        <div className="space-y-1 text-xs">
          <div className="text-sm font-semibold">Turn {turn.index}</div>
          <div className="flex items-center gap-2 text-muted-foreground">
            <span>stop:</span>
            <Badge variant="outline">{turn.stop_reason ?? '—'}</Badge>
            <span>tool calls: {turn.tool_calls_count}</span>
          </div>
        </div>
      </Card>
      {turn.messages.length > 0 && (
        <Section
          title={t('messages')}
          icon={<MessageSquare className="size-4 text-muted-foreground" />}
        >
          <MessageList items={turn.messages} />
        </Section>
      )}
      {turn.output_messages.length > 0 && (
        <Section
          title={t('output')}
          icon={<MessageSquare className="size-4 text-muted-foreground" />}
        >
          <MessageList items={turn.output_messages} />
        </Section>
      )}
    </div>
  )
}
