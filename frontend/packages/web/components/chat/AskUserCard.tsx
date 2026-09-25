'use client'

import { useState, useEffect } from 'react'
import { useTranslations } from 'next-intl'
import { Clock, MessageCircleQuestion, Send, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import type { AskOption, PendingAsk, AskQuestion } from '@cubeplex/core'

interface AskUserCardProps {
  pending: PendingAsk
  onSubmit: (answers: Record<string, string | string[]>) => Promise<void>
  onCancel?: () => Promise<void>
}

function customAnswerKey(questionKey: string, optionValue: string): string {
  return `${questionKey}\0${optionValue}`
}

function customAnswerText(
  custom: Record<string, string>,
  questionKey: string,
  optionValue: string,
): string {
  return (custom[customAnswerKey(questionKey, optionValue)] ?? '').trim()
}

function submittedAnswer(
  question: AskQuestion,
  raw: string | string[],
  custom: Record<string, string>,
): string | string[] {
  if (!question.options) return raw
  if (question.multi_select) {
    const selected = Array.isArray(raw) ? raw : []
    return selected.map((value) => {
      const option = question.options?.find((opt) => opt.value === value)
      if (!option?.allow_input) return value
      return customAnswerText(custom, question.key, value)
    })
  }
  const selected = typeof raw === 'string' ? raw : ''
  const option = question.options.find((opt) => opt.value === selected)
  if (!option?.allow_input) return selected
  return customAnswerText(custom, question.key, selected)
}

function blocksSubmit(
  question: AskQuestion,
  raw: string | string[],
  custom: Record<string, string>,
): boolean {
  if (!question.options) {
    return question.required && (typeof raw !== 'string' || raw === '')
  }
  if (question.multi_select) {
    const selected = Array.isArray(raw) ? raw : []
    if (selected.length === 0) return question.required
    return selected.some((value) => {
      const option = question.options?.find((opt) => opt.value === value)
      return Boolean(option?.allow_input) && customAnswerText(custom, question.key, value) === ''
    })
  }
  const selected = typeof raw === 'string' ? raw : ''
  if (selected === '') return question.required
  const option = question.options.find((opt) => opt.value === selected)
  return Boolean(option?.allow_input) && customAnswerText(custom, question.key, selected) === ''
}

function CustomAnswerInput({
  questionKey,
  option,
  value,
  onChange,
}: {
  questionKey: string
  option: AskOption
  value: string
  onChange: (text: string) => void
}) {
  const t = useTranslations('askUser')
  return (
    <Input
      id={`${questionKey}-${option.value}-custom`}
      aria-label={t('customInput', { label: option.label })}
      placeholder={t('customPlaceholder')}
      value={value}
      autoFocus
      onChange={(e) => onChange(e.target.value)}
      className="ml-6 h-8 text-sm"
    />
  )
}

function QuestionField({
  question,
  value,
  custom,
  onChange,
  onCustomChange,
}: {
  question: AskQuestion
  value: string | string[]
  custom: Record<string, string>
  onChange: (v: string | string[]) => void
  onCustomChange: (optionValue: string, text: string) => void
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
        {question.options.map((opt) => {
          const checked = selected.includes(opt.value)
          return (
            <div key={opt.value} className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <Checkbox
                  id={`${question.key}-${opt.value}`}
                  checked={checked}
                  onCheckedChange={(nextChecked) => {
                    const next = nextChecked
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
              {checked && opt.allow_input ? (
                <CustomAnswerInput
                  questionKey={question.key}
                  option={opt}
                  value={custom[customAnswerKey(question.key, opt.value)] ?? ''}
                  onChange={(text) => onCustomChange(opt.value, text)}
                />
              ) : null}
            </div>
          )
        })}
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
        {question.options.map((opt) => {
          const checked = typeof value === 'string' && value === opt.value
          return (
            <div key={opt.value} className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <RadioGroupItem value={opt.value} id={`${question.key}-${opt.value}`} />
                <Label
                  htmlFor={`${question.key}-${opt.value}`}
                  className="cursor-pointer text-sm text-foreground"
                >
                  {opt.label}
                </Label>
              </div>
              {checked && opt.allow_input ? (
                <CustomAnswerInput
                  questionKey={question.key}
                  option={opt}
                  value={custom[customAnswerKey(question.key, opt.value)] ?? ''}
                  onChange={(text) => onCustomChange(opt.value, text)}
                />
              ) : null}
            </div>
          )
        })}
      </RadioGroup>
    </div>
  )
}

export function AskUserCard({ pending, onSubmit, onCancel }: AskUserCardProps) {
  const t = useTranslations('askUser')
  const [answers, setAnswers] = useState<Record<string, string | string[]>>(() => {
    const init: Record<string, string | string[]> = {}
    for (const q of pending.questions) {
      init[q.key] = q.multi_select ? [] : ''
    }
    return init
  })
  const [customAnswers, setCustomAnswers] = useState<Record<string, string>>({})
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

  const setCustomAnswer = (questionKey: string, optionValue: string, text: string) => {
    setCustomAnswers((prev) => ({
      ...prev,
      [customAnswerKey(questionKey, optionValue)]: text,
    }))
  }

  const hasUnfilledRequired = pending.questions.some((q) =>
    blocksSubmit(q, answers[q.key] ?? (q.multi_select ? [] : ''), customAnswers),
  )

  const handleSubmit = async () => {
    if (submitting || hasUnfilledRequired) return
    const payload: Record<string, string | string[]> = {}
    for (const q of pending.questions) {
      payload[q.key] = submittedAnswer(
        q,
        answers[q.key] ?? (q.multi_select ? [] : ''),
        customAnswers,
      )
    }
    setSubmitting(true)
    try {
      await onSubmit(payload)
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
    <div className="my-2 rounded-lg border border-info-border bg-info-surface p-3">
      <div className="flex items-start gap-2">
        <MessageCircleQuestion className="mt-0.5 h-4 w-4 shrink-0 text-info-fg" aria-hidden />
        <div className="flex flex-1 flex-col gap-3">
          {pending.questions.map((q) => (
            <QuestionField
              key={q.key}
              question={q}
              value={answers[q.key] ?? (q.multi_select ? [] : '')}
              custom={customAnswers}
              onChange={(v) => setAnswer(q.key, v)}
              onCustomChange={(optionValue, text) => setCustomAnswer(q.key, optionValue, text)}
            />
          ))}
        </div>
        {secondsLeft !== null && secondsLeft > 0 && (
          <span className="inline-flex shrink-0 items-center gap-1 text-xs tabular-nums text-info-fg">
            <Clock className="h-3 w-3" />
            {secondsLeft}s
          </span>
        )}
      </div>
      <div className="mt-3 flex items-center gap-2 pl-6">
        <Button
          size="sm"
          className="gap-1"
          disabled={submitting || cancelling || hasUnfilledRequired}
          onClick={handleSubmit}
        >
          <Send className="h-3.5 w-3.5" />
          {submitting ? t('submitting') : t('submit')}
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
            {cancelling ? t('cancelling') : t('cancel')}
          </Button>
        )}
      </div>
    </div>
  )
}
