'use client'

import { useState } from 'react'
import { ChevronDown, ChevronRight, Bot } from 'lucide-react'
import type { AgentStream } from '@cubebox/core'
import type { ToolCallEvent } from '@cubebox/core'

interface Props {
  agentId: string
  stream: AgentStream
  isRunning: boolean
}

export function SubAgentCard({ agentId, stream, isRunning }: Props) {
  const [open, setOpen] = useState(true)
  const name = stream.name ?? agentId

  return (
    <div className="border border-border rounded-lg my-2 overflow-hidden bg-muted/20">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-sm text-muted-foreground hover:bg-muted/30 transition-colors"
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <Bot className="h-3 w-3" />
        <span className="font-medium">{name}</span>
        {isRunning && (
          <span className="ml-auto flex gap-0.5">
            {[0, 1, 2].map((i) => (
              <span
                key={i}
                className="w-1 h-1 rounded-full bg-muted-foreground animate-bounce"
                style={{ animationDelay: `${i * 150}ms` }}
              />
            ))}
          </span>
        )}
      </button>

      {open && (
        <div className="px-3 pb-3 pt-1 space-y-1">
          {stream.toolCalls.map((tc, i) => (
            <div key={i} className="text-xs font-mono text-muted-foreground truncate">
              <span className="text-foreground/60">{tc.data.name}</span>
              {' '}
              <span className="opacity-60">
                {JSON.stringify(tc.data.arguments).slice(0, 80)}
              </span>
            </div>
          ))}
          {stream.text && (
            <p className="text-sm text-foreground/80 whitespace-pre-wrap">{stream.text}</p>
          )}
        </div>
      )}
    </div>
  )
}
