'use client'

import { FileText, MessageSquare, Wrench } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import type { AgentPayload } from '../types'
import { MessageList } from './MessageList'
import { Section } from './Section'

interface Props {
  agent: AgentPayload
  // Distinct chat models used anywhere in this agent run - a run can call
  // more than one model across turns, so this is a list, not a single value.
  models: string[]
}

// cubepi prefixes a provider name it doesn't canonically recognize with
// "unknown:" (see cubepi/tracing/schema.py::map_provider_name) - real,
// intentional signal, not a bug. Show the actual name as the primary text,
// full value (with the prefix) as a tooltip so the signal isn't lost.
function formatProvider(provider: string): { label: string; title: string } {
  const idx = provider.indexOf(':')
  return idx === -1
    ? { label: provider, title: provider }
    : { label: provider.slice(idx + 1), title: provider }
}

export function AgentCard({ agent, models }: Props) {
  const t = useTranslations('adminTraces.sections')
  const provider = agent.provider ? formatProvider(agent.provider) : null

  return (
    <div className="space-y-3">
      {(provider || models.length > 0 || agent.tools.length > 0) && (
        <Section
          title={t('agentOverview')}
          icon={<Wrench className="size-4 text-muted-foreground" />}
        >
          <div className="space-y-2 text-xs">
            {(provider || models.length > 0) && (
              <div>
                {provider && (
                  <span>
                    <span className="text-muted-foreground">{t('provider')}: </span>
                    <span className="font-mono" title={provider.title}>
                      {provider.label}
                    </span>
                  </span>
                )}
                {provider && models.length > 0 && (
                  <span className="text-muted-foreground"> · </span>
                )}
                {models.length > 0 && (
                  <span>
                    <span className="text-muted-foreground">{t('model')}: </span>
                    <span className="font-mono">{models.join(', ')}</span>
                  </span>
                )}
              </div>
            )}
            {agent.tools.length > 0 && (
              <div>
                <div className="mb-1 text-muted-foreground">{t('tools')}</div>
                <div className="flex flex-wrap gap-1">
                  {agent.tools.map((name) => (
                    <Badge key={name} variant="outline">
                      {name}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </div>
        </Section>
      )}
      <Section
        title={t('system')}
        defaultOpen={false}
        icon={<FileText className="size-4 text-muted-foreground" />}
      >
        <MessageList items={agent.system_instructions} />
      </Section>
      <Section
        title={t('messages')}
        icon={<MessageSquare className="size-4 text-muted-foreground" />}
      >
        <MessageList items={agent.messages} />
      </Section>
      <Section
        title={t('output')}
        icon={<MessageSquare className="size-4 text-muted-foreground" />}
      >
        <MessageList items={agent.output_messages} />
      </Section>
    </div>
  )
}
