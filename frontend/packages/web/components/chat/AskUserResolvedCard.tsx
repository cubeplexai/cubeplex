'use client'

import { Check } from 'lucide-react'
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

function selectedValues(value: unknown): Set<string> {
  if (Array.isArray(value)) return new Set(value.map(String))
  if (value === undefined || value === null) return new Set()
  return new Set([String(value)])
}

function OptionsList({
  question,
  answer,
  hasAnswer,
}: {
  question: AskQuestion
  answer: unknown
  hasAnswer: boolean
}) {
  const selected = hasAnswer ? selectedValues(answer) : new Set<string>()
  return (
    <div className="flex flex-col gap-1 pl-2">
      {question.options!.map((opt) => {
        const isSelected = selected.has(opt.value)
        return (
          <div
            key={opt.value}
            className={
              'flex items-center gap-2 text-sm ' +
              (isSelected ? 'text-foreground font-medium' : 'text-muted-foreground/70')
            }
          >
            <span
              className={
                'inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border ' +
                (isSelected ? 'border-primary bg-primary text-primary-foreground' : 'border-border')
              }
            >
              {isSelected && <Check className="h-2.5 w-2.5" />}
            </span>
            <span>{opt.label}</span>
          </div>
        )
      })}
    </div>
  )
}

function FreeTextAnswer({ answer, hasAnswer }: { answer: unknown; hasAnswer: boolean }) {
  const text = (() => {
    if (!hasAnswer) return '—'
    if (Array.isArray(answer)) return answer.map(String).join('、')
    if (answer === undefined || answer === null) return '—'
    return String(answer)
  })()
  return <div className="text-sm text-muted-foreground pl-2 border-l-2 border-border">{text}</div>
}

export function AskUserResolvedCard({ questions, resultContent }: AskUserResolvedCardProps) {
  const parsed = parseAnswers(resultContent)

  return (
    <div className="px-4 py-3 flex flex-col gap-3">
      {questions.map((q) => {
        const answer = parsed.byKey[q.key]
        const hasAnswer = parsed.ok && answer !== undefined
        return (
          <div key={q.key} className="flex flex-col gap-1.5">
            <div className="text-sm font-medium text-foreground">{q.prompt}</div>
            {q.options && q.options.length > 0 ? (
              <OptionsList question={q} answer={answer} hasAnswer={hasAnswer} />
            ) : (
              <FreeTextAnswer answer={answer} hasAnswer={hasAnswer} />
            )}
          </div>
        )
      })}
      {!parsed.ok && parsed.fallback && (
        <div className="text-xs text-muted-foreground italic">{parsed.fallback}</div>
      )}
    </div>
  )
}
