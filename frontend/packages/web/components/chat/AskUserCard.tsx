'use client'

import { useState, useEffect } from 'react'
import { Clock, Send, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import type { PendingAsk, AskQuestion } from '@cubebox/core'

interface AskUserCardProps {
  pending: PendingAsk
  onSubmit: (answers: Record<string, string | string[]>) => Promise<void>
  onCancel?: () => Promise<void>
}

function QuestionField({
  question,
  value,
  onChange,
}: {
  question: AskQuestion
  value: string | string[]
  onChange: (v: string | string[]) => void
}) {
  if (!question.options) {
    return (
      <div className="flex flex-col gap-1">
        <Label className="text-sm font-medium text-foreground">{question.prompt}</Label>
        <Input
          value={typeof value === 'string' ? value : ''}
          onChange={(e) => onChange(e.target.value)}
          className="h-8 text-sm"
        />
      </div>
    )
  }

  if (question.multi_select) {
    const selected = Array.isArray(value) ? value : []
    return (
      <div className="flex flex-col gap-1.5">
        <Label className="text-sm font-medium text-foreground">{question.prompt}</Label>
        {question.options.map((opt) => (
          <div key={opt.value} className="flex items-center gap-2">
            <Checkbox
              id={`${question.key}-${opt.value}`}
              checked={selected.includes(opt.value)}
              onCheckedChange={(checked) => {
                const next = checked
                  ? [...selected, opt.value]
                  : selected.filter((v) => v !== opt.value)
                onChange(next)
              }}
            />
            <Label
              htmlFor={`${question.key}-${opt.value}`}
              className="cursor-pointer text-sm text-foreground"
            >
              {opt.label}
            </Label>
          </div>
        ))}
      </div>
    )
  }

  // Single select — radio group
  return (
    <div className="flex flex-col gap-1.5">
      <Label className="text-sm font-medium text-foreground">{question.prompt}</Label>
      <RadioGroup
        value={typeof value === 'string' ? value : ''}
        onValueChange={(v) => onChange(v)}
        className="flex flex-col gap-1"
      >
        {question.options.map((opt) => (
          <div key={opt.value} className="flex items-center gap-2">
            <RadioGroupItem value={opt.value} id={`${question.key}-${opt.value}`} />
            <Label
              htmlFor={`${question.key}-${opt.value}`}
              className="cursor-pointer text-sm text-foreground"
            >
              {opt.label}
            </Label>
          </div>
        ))}
      </RadioGroup>
    </div>
  )
}

export function AskUserCard({ pending, onSubmit, onCancel }: AskUserCardProps) {
  const [answers, setAnswers] = useState<Record<string, string | string[]>>(() => {
    const init: Record<string, string | string[]> = {}
    for (const q of pending.questions) {
      init[q.key] = q.multi_select ? [] : ''
    }
    return init
  })
  const [submitting, setSubmitting] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  // Initialise to null to avoid SSR/CSR hydration mismatch (Date.now() differs).
  // The first useEffect sets the real value after mount.
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null)

  useEffect(() => {
    if (pending.timeout_seconds === null) return
    const computeLeft = () => {
      const elapsed = Math.floor((Date.now() - pending.requestedAt) / 1000)
      return Math.max(0, pending.timeout_seconds! - elapsed)
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSecondsLeft(computeLeft())
    const id = setInterval(() => setSecondsLeft(computeLeft()), 1000)
    return () => clearInterval(id)
  }, [pending.timeout_seconds, pending.requestedAt])

  const setAnswer = (key: string, value: string | string[]) => {
    setAnswers((prev) => ({ ...prev, [key]: value }))
  }

  const hasUnfilledRequired = pending.questions.some((q) => {
    if (!q.required) return false
    const v = answers[q.key]
    return Array.isArray(v) ? v.length === 0 : v === ''
  })

  const handleSubmit = async () => {
    if (submitting || hasUnfilledRequired) return
    setSubmitting(true)
    try {
      await onSubmit(answers)
    } catch {
      setSubmitting(false)
    }
  }

  const handleCancel = async () => {
    if (!onCancel || cancelling || submitting) return
    setCancelling(true)
    try {
      await onCancel()
    } catch {
      setCancelling(false)
    }
  }

  return (
    <div className="my-2 rounded-lg border border-blue-200 bg-blue-50 p-3 dark:border-blue-800 dark:bg-blue-950/30">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium text-blue-800 dark:text-blue-200">
        <Clock className="h-4 w-4 shrink-0" />
        <span>Agent is asking for your input</span>
        {secondsLeft !== null && secondsLeft > 0 && (
          <span className="ml-auto tabular-nums text-blue-600 dark:text-blue-400">
            {secondsLeft}s
          </span>
        )}
      </div>
      <div className="flex flex-col gap-3">
        {pending.questions.map((q) => (
          <QuestionField
            key={q.key}
            question={q}
            value={answers[q.key] ?? (q.multi_select ? [] : '')}
            onChange={(v) => setAnswer(q.key, v)}
          />
        ))}
      </div>
      <div className="mt-3 flex items-center gap-2">
        <Button
          size="sm"
          className="gap-1"
          disabled={submitting || cancelling || hasUnfilledRequired}
          onClick={handleSubmit}
        >
          <Send className="h-3.5 w-3.5" />
          {submitting ? 'Sending…' : 'Submit'}
        </Button>
        {onCancel && (
          <Button
            size="sm"
            variant="ghost"
            className="gap-1 text-muted-foreground hover:text-foreground"
            disabled={submitting || cancelling}
            onClick={handleCancel}
          >
            <X className="h-3.5 w-3.5" />
            {cancelling ? 'Cancelling…' : 'Cancel'}
          </Button>
        )}
      </div>
    </div>
  )
}
