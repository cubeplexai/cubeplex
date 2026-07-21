'use client'

import { Cpu, FileText, Gauge, Hash, MessageSquare, Wrench } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import type { LlmCallPayload } from '../types'
import { JsonBlock } from './JsonBlock'
import { MessageList } from './MessageList'
import { Section } from './Section'

interface Props {
  llm: LlmCallPayload
}

function TokenTile({ label, value, tone }: { label: string; value: number; tone: TokenTone }) {
  return (
    <div className={`rounded-lg p-3 text-center ${TOKEN_TONE_CLASSES[tone]}`}>
      <div className="text-xl font-semibold tabular-nums">{value.toLocaleString()}</div>
      <div className="text-[11px] tracking-wide uppercase opacity-80">{label}</div>
    </div>
  )
}

type TokenTone = 'info' | 'success' | 'warning' | 'muted'

const TOKEN_TONE_CLASSES: Record<TokenTone, string> = {
  info: 'bg-info-surface text-info-fg',
  success: 'bg-success-surface text-success-fg',
  warning: 'bg-warning-surface text-warning-fg',
  muted: 'bg-muted text-muted-foreground',
}

export function LlmCard({ llm }: Props) {
  const t = useTranslations('adminTraces.sections')
  const hasCache = llm.tokens.cache_read > 0 || llm.tokens.cache_write > 0

  return (
    <div className="space-y-3">
      <Section title={t('model')} icon={<Cpu className="size-4 text-muted-foreground" />}>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
          <dt className="text-muted-foreground">Model</dt>
          <dd className="font-mono">{llm.model}</dd>
          <dt className="text-muted-foreground">Provider</dt>
          <dd className="font-mono">{llm.provider ?? '—'}</dd>
          <dt className="text-muted-foreground">Max tokens</dt>
          <dd className="font-mono">{llm.request_max_tokens ?? '—'}</dd>
          <dt className="text-muted-foreground">Temperature</dt>
          <dd className="font-mono">{llm.request_temperature ?? '—'}</dd>
          <dt className="text-muted-foreground">Stream</dt>
          <dd className="font-mono">{String(llm.request_stream ?? '—')}</dd>
          <dt className="text-muted-foreground">Finish</dt>
          <dd className="flex flex-wrap gap-1">
            {llm.finish_reasons.length > 0
              ? llm.finish_reasons.map((r) => (
                  <Badge key={r} variant="outline">
                    {r}
                  </Badge>
                ))
              : '—'}
          </dd>
        </dl>
      </Section>

      <Section title={t('tokens')} icon={<Hash className="size-4 text-muted-foreground" />}>
        <div className={`grid gap-2 ${hasCache ? 'grid-cols-4' : 'grid-cols-2'}`}>
          <TokenTile label={t('tokensInput')} value={llm.tokens.input} tone="info" />
          <TokenTile label={t('tokensOutput')} value={llm.tokens.output} tone="success" />
          {llm.tokens.cache_read > 0 && (
            <TokenTile label={t('tokensCacheRead')} value={llm.tokens.cache_read} tone="warning" />
          )}
          {llm.tokens.cache_write > 0 && (
            <TokenTile label={t('tokensCacheWrite')} value={llm.tokens.cache_write} tone="muted" />
          )}
        </div>
      </Section>

      {llm.tools.length > 0 && (
        <Section
          title={t('tools')}
          defaultOpen={false}
          icon={<Wrench className="size-4 text-muted-foreground" />}
        >
          <ul className="space-y-2 text-xs">
            {llm.tools.map((tool) => (
              <li key={tool.name} className="rounded border border-border/60 p-2">
                <div className="font-mono font-semibold">{tool.name}</div>
                {tool.description && (
                  <div className="text-muted-foreground">{tool.description}</div>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section
        title={t('system')}
        defaultOpen={false}
        icon={<FileText className="size-4 text-muted-foreground" />}
      >
        <MessageList items={llm.system_instructions} />
      </Section>
      <Section
        title={t('messages')}
        icon={<MessageSquare className="size-4 text-muted-foreground" />}
      >
        <MessageList items={llm.messages} />
      </Section>
      <Section
        title={t('output')}
        icon={<MessageSquare className="size-4 text-muted-foreground" />}
      >
        <MessageList items={llm.output_messages} />
      </Section>
      <Section title={t('rawRequest')} defaultOpen={false}>
        <JsonBlock value={llm.raw_request} />
      </Section>
      <Section title={t('rawResponse')} defaultOpen={false}>
        <JsonBlock value={llm.raw_response} />
      </Section>

      <Section
        title={t('performance')}
        defaultOpen={false}
        icon={<Gauge className="size-4 text-muted-foreground" />}
      >
        <dl className="grid grid-cols-2 gap-2 text-xs">
          <dt className="text-muted-foreground">Time to first chunk</dt>
          <dd className="font-mono">
            {llm.time_to_first_chunk_seconds != null
              ? `${llm.time_to_first_chunk_seconds.toFixed(2)} s`
              : '—'}
          </dd>
          <dt className="text-muted-foreground">Response ID</dt>
          <dd className="font-mono">{llm.response_id ?? '—'}</dd>
        </dl>
      </Section>
    </div>
  )
}
