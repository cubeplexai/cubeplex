'use client'

import { MessageCircleQuestion } from 'lucide-react'
import type { AskQuestion } from '@cubebox/core'

interface AskUserResolvedCardProps {
  questions: AskQuestion[]
  /** Raw `tool_result.content` body from cubepi. Backend formats it as
   * `User answers:\n{json}` on success, or a free-text string on
   * cancel / timeout / error. We try to parse the JSON to render
   * per-question answers; we fall back to displaying the raw body. */
  resultContent: string | null
}

interface ParsedAnswers {
  ok: boolean
  byKey: Record<string, unknown>
  fallback: string | null
}

function parseAnswers(raw: string | null): ParsedAnswers {
  if (!raw) return { ok: false, byKey: {}, fallback: null }
  const marker = 'User answers:'
  const idx = raw.indexOf(marker)
  if (idx === -1) return { ok: false, byKey: {}, fallback: raw.trim() }
  const jsonStr = raw.slice(idx + marker.length).trim()
  try {
    const parsed = JSON.parse(jsonStr) as Record<string, unknown>
    return { ok: true, byKey: parsed, fallback: null }
  } catch {
    return { ok: false, byKey: {}, fallback: raw.trim() }
  }
}

function answerLabel(question: AskQuestion, value: unknown): string {
  if (value === undefined || value === null) return '—'
  if (Array.isArray(value)) {
    if (question.options) {
      return value
        .map((v) => question.options?.find((opt) => opt.value === v)?.label ?? String(v))
        .join('、')
    }
    return value.map(String).join('、')
  }
  if (question.options) {
    return question.options.find((opt) => opt.value === value)?.label ?? String(value)
  }
  return String(value)
}

export function AskUserResolvedCard({ questions, resultContent }: AskUserResolvedCardProps) {
  const parsed = parseAnswers(resultContent)

  return (
    <div className="px-4 py-3 flex flex-col gap-3">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <MessageCircleQuestion className="h-3.5 w-3.5" />
        <span>ask_user</span>
      </div>
      <div className="flex flex-col gap-2.5">
        {questions.map((q) => {
          const answer = parsed.byKey[q.key]
          const showAnswer = parsed.ok || resultContent !== null
          return (
            <div key={q.key} className="flex flex-col gap-1">
              <div className="text-sm font-medium text-foreground">{q.prompt}</div>
              {showAnswer && (
                <div className="text-sm text-muted-foreground pl-2 border-l-2 border-border">
                  {parsed.ok ? answerLabel(q, answer) : '—'}
                </div>
              )}
            </div>
          )
        })}
      </div>
      {!parsed.ok && parsed.fallback && (
        <div className="text-xs text-muted-foreground italic">{parsed.fallback}</div>
      )}
    </div>
  )
}
