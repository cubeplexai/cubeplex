'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  Bot,
  Brain,
  ChevronDown,
  ChevronRight,
  Settings,
  Terminal,
  User,
  Wrench,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import type { ChatMessage } from '../types'
import { JsonBlock } from './JsonBlock'
import { MessageText } from './MessageText'

interface Props {
  items: ChatMessage[]
}

// Role → icon + left-accent color. Matches the semantic tokens already used
// elsewhere in this directory (info/success surfaces), not a new palette.
const ROLE_STYLE: Record<string, { icon: typeof User; accent: string }> = {
  user: { icon: User, accent: 'border-l-info-solid' },
  assistant: { icon: Bot, accent: 'border-l-primary' },
  system: { icon: Settings, accent: 'border-l-muted-foreground/40' },
  tool: { icon: Terminal, accent: 'border-l-success-solid' },
}

function str(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

const KNOWN_ROLES = new Set(['user', 'assistant', 'system', 'tool'])

// Matches AssistantMessage.tsx's completed-state ReasoningBlock almost
// exactly (Brain icon, chevron, italic muted text, left border) so a
// "reasoning" part here reads as the same concept the live chat UI already
// shows the user - just without the streaming ticker (traces are historical).
function ReasoningPart({ content }: { content: string }) {
  const t = useTranslations('adminTraces.sections')
  const [open, setOpen] = useState(false)
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
      >
        {open ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
        <Brain className="size-3 text-muted-foreground/70" />
        <span>{t('reasoning')}</span>
      </button>
      {open && (
        <div className="mt-1.5 max-h-64 overflow-y-auto border-l-2 border-border/50 pl-4">
          <p className="text-xs leading-relaxed whitespace-pre-wrap text-muted-foreground/70 italic">
            {content}
          </p>
        </div>
      )}
    </div>
  )
}

function ToolCallPart({ part }: { part: Record<string, unknown> }) {
  const name = str(part.name) || '?'
  return (
    <div className="rounded border border-border/60 bg-muted/20 p-2">
      <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold">
        <Wrench className="size-3.5 text-muted-foreground" />
        <span className="font-mono">{name}</span>
      </div>
      <JsonBlock value={JSON.stringify(part.arguments ?? {}, null, 2)} />
    </div>
  )
}

function ToolResultPart({ part }: { part: Record<string, unknown> }) {
  return (
    <div className="rounded border border-border/60 bg-muted/20 p-2">
      <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
        <Terminal className="size-3.5" />
        <span>result</span>
      </div>
      <JsonBlock value={str(part.result)} language="text" />
    </div>
  )
}

function MessagePart({ part }: { part: Record<string, unknown> }) {
  const type = str(part.type)
  switch (type) {
    case 'text':
      return <MessageText>{str(part.content)}</MessageText>
    case 'reasoning':
      return <ReasoningPart content={str(part.content)} />
    case 'tool_call':
      return <ToolCallPart part={part} />
    case 'tool_call_response':
      return <ToolResultPart part={part} />
    default:
      return <Badge variant="outline">[{type || 'unknown'}]</Badge>
  }
}

export function MessageList({ items }: Props) {
  const t = useTranslations('adminTraces.sections')
  if (!items.length) return <div className="text-xs text-muted-foreground">—</div>
  return (
    <div className="space-y-3">
      {items.map((m, i) => {
        const style = ROLE_STYLE[m.role] ?? ROLE_STYLE.system
        const Icon = style.icon
        return (
          <div
            key={i}
            className={`space-y-2 rounded-r-lg border-l-2 bg-muted/10 py-2 pr-3 pl-3 ${style.accent}`}
          >
            <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
              <Icon className="size-3.5" />
              <span>
                {KNOWN_ROLES.has(m.role)
                  ? t(
                      `roles.${m.role}` as
                        'roles.user' | 'roles.assistant' | 'roles.system' | 'roles.tool',
                    )
                  : m.role}
              </span>
            </div>
            <div className="space-y-2">
              {m.parts.map((part, j) => (
                <MessagePart key={j} part={part} />
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}
