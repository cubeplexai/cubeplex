'use client'

import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { useTranslations } from 'next-intl'

import type { ChatMessage, LlmCallPayload } from '../types'
import { JsonBlock } from './JsonBlock'

interface Props {
  llm: LlmCallPayload
}

function Section({
  title,
  defaultOpen = true,
  children,
}: {
  title: string
  defaultOpen?: boolean
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded border border-border bg-card">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between px-3 py-2 text-sm font-medium"
      >
        <span>{title}</span>
        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
      </button>
      {open && <div className="border-t border-border px-3 py-3">{children}</div>}
    </div>
  )
}

function Messages({ items }: { items: ChatMessage[] }) {
  if (!items.length) return <div className="text-xs text-muted-foreground">—</div>
  return (
    <div className="space-y-3">
      {items.map((m, i) => (
        <div key={i} className="rounded border border-border/60 bg-muted/20 p-2">
          <div className="mb-1 text-xs font-semibold uppercase text-muted-foreground">{m.role}</div>
          <JsonBlock value={JSON.stringify(m.parts, null, 2)} />
        </div>
      ))}
    </div>
  )
}

export function LlmCard({ llm }: Props) {
  const t = useTranslations('adminTraces.sections')
  return (
    <div className="space-y-3">
      <Section title={t('model')}>
        <dl className="grid grid-cols-2 gap-2 text-xs">
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
          <dd className="font-mono">{llm.finish_reasons.join(', ') || '—'}</dd>
        </dl>
      </Section>

      <Section title={t('tokens')}>
        <dl className="grid grid-cols-4 gap-2 text-center text-xs">
          <div>
            <dt className="text-muted-foreground">input</dt>
            <dd className="font-mono">{llm.tokens.input}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">output</dt>
            <dd className="font-mono">{llm.tokens.output}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">cache read</dt>
            <dd className="font-mono">{llm.tokens.cache_read}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">cache write</dt>
            <dd className="font-mono">{llm.tokens.cache_write}</dd>
          </div>
        </dl>
      </Section>

      {llm.tools.length > 0 && (
        <Section title={t('tools')} defaultOpen={false}>
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

      <Section title={t('system')} defaultOpen={false}>
        <Messages items={llm.system_instructions} />
      </Section>
      <Section title={t('messages')}>
        <Messages items={llm.messages} />
      </Section>
      <Section title={t('output')}>
        <Messages items={llm.output_messages} />
      </Section>
      <Section title={t('rawRequest')} defaultOpen={false}>
        <JsonBlock value={llm.raw_request} />
      </Section>
      <Section title={t('rawResponse')} defaultOpen={false}>
        <JsonBlock value={llm.raw_response} />
      </Section>

      <Section title={t('performance')} defaultOpen={false}>
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
