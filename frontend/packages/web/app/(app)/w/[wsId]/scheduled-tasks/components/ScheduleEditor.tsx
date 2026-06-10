'use client'

import { useEffect, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
import type {
  DailySchedule,
  IntervalSchedule,
  MonthlySchedule,
  ScheduleEditorValue,
  ScheduleState,
  WeeklySchedule,
} from '../lib/schedulePayload'

// ── Frequency pills ──────────────────────────────────────────────────────────

type FreqKind = 'daily' | 'weekly' | 'monthly' | 'interval' | 'once'

const FREQ_KEYS = [
  { kind: 'daily', labelKey: 'freqDaily' },
  { kind: 'weekly', labelKey: 'freqWeekly' },
  { kind: 'monthly', labelKey: 'freqMonthly' },
  { kind: 'interval', labelKey: 'freqInterval' },
  { kind: 'once', labelKey: 'freqOnce' },
] as const

function FrequencyPills({
  value,
  onChange,
}: {
  value: FreqKind
  onChange: (kind: FreqKind) => void
}) {
  const t = useTranslations('scheduledTasks')
  return (
    <div className="flex flex-wrap gap-1.5">
      {FREQ_KEYS.map(({ kind, labelKey }) => (
        <button
          key={kind}
          type="button"
          onClick={() => onChange(kind)}
          className={cn(
            'rounded-full border px-3 py-1 text-xs font-medium transition-colors',
            value === kind
              ? 'border-primary bg-primary/15 text-primary'
              : 'border-border text-muted-foreground hover:border-primary/40 hover:text-foreground',
          )}
        >
          {t(labelKey)}
        </button>
      ))}
    </div>
  )
}

// ── Time input ───────────────────────────────────────────────────────────────

function TimeInput({
  hour,
  minute,
  onChange,
}: {
  hour: number
  minute: number
  onChange: (hour: number, minute: number) => void
}) {
  const value = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
  return (
    <Input
      type="time"
      value={value}
      onChange={(e) => {
        const [h, m] = e.target.value.split(':').map(Number)
        if (!isNaN(h) && !isNaN(m)) onChange(h, m)
      }}
      className="max-w-[110px]"
    />
  )
}

// ── Timezone input ───────────────────────────────────────────────────────────

function TimezoneInput({ value, onChange }: { value: string; onChange: (tz: string) => void }) {
  const t = useTranslations('scheduledTasks')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const [error, setError] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editing) inputRef.current?.focus()
  }, [editing])

  function validate(tz: string): boolean {
    try {
      Intl.DateTimeFormat(undefined, { timeZone: tz })
      return true
    } catch {
      return false
    }
  }

  function commit() {
    if (validate(draft)) {
      onChange(draft)
      setError(false)
      setEditing(false)
    } else {
      setError(true)
    }
  }

  if (!editing) {
    return (
      <button
        type="button"
        onClick={() => {
          setDraft(value)
          setEditing(true)
        }}
        className="inline-flex items-center gap-1 rounded-md border border-border/50 bg-muted/40 px-2 py-0.5 text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground"
      >
        🌐 {value}
      </button>
    )
  }

  return (
    <div className="flex items-center gap-1.5">
      <Input
        ref={inputRef}
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value)
          setError(false)
        }}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            commit()
          }
        }}
        className={cn('h-7 max-w-[200px] text-xs', error && 'border-destructive')}
        placeholder="Asia/Shanghai"
      />
      {error && <span className="text-xs text-destructive">{t('invalidTimezone')}</span>}
    </div>
  )
}

// ── End-date input ───────────────────────────────────────────────────────────

function EndDateInput({
  value,
  onChange,
}: {
  value: string | null
  onChange: (date: string | null) => void
}) {
  const t = useTranslations('scheduledTasks')
  const enabled = value !== null
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          onClick={() => onChange(enabled ? null : new Date().toISOString().slice(0, 10))}
          className={cn(
            'relative h-4 w-7 rounded-full transition-colors',
            enabled ? 'bg-primary/40' : 'bg-border',
          )}
        >
          <span
            className={cn(
              'absolute top-0.5 h-3 w-3 rounded-full transition-all',
              enabled ? 'left-3.5 bg-primary' : 'left-0.5 bg-muted-foreground',
            )}
          />
        </button>
        <Label className="text-xs font-normal text-muted-foreground">
          {t('endDate')} <span className="text-primary text-[10px]">{t('optional')}</span>
        </Label>
      </div>
      {enabled && (
        <div className="flex items-center gap-2">
          <Input
            type="date"
            value={value ?? ''}
            onChange={(e) => onChange(e.target.value || null)}
            className="max-w-[160px] text-xs"
            min={new Date().toISOString().slice(0, 10)}
          />
          <span className="text-xs text-muted-foreground">{t('endDateStop')}</span>
        </div>
      )}
      {!enabled && <p className="text-xs italic text-muted-foreground">{t('endDateUnset')}</p>}
    </div>
  )
}

// ── Weekday picker ───────────────────────────────────────────────────────────

const WEEKDAY_KEYS = [
  'weekdaySun',
  'weekdayMon',
  'weekdayTue',
  'weekdayWed',
  'weekdayThu',
  'weekdayFri',
  'weekdaySat',
] as const

function WeekdayPicker({
  value,
  onChange,
}: {
  value: number[]
  onChange: (days: number[]) => void
}) {
  const t = useTranslations('scheduledTasks')
  function toggle(day: number) {
    const next = value.includes(day) ? value.filter((d) => d !== day) : [...value, day]
    if (next.length > 0) onChange(next)
  }
  return (
    <div className="flex gap-1">
      {WEEKDAY_KEYS.map((key, i) => (
        <button
          key={i}
          type="button"
          onClick={() => toggle(i)}
          className={cn(
            'flex h-8 w-8 items-center justify-center rounded-full border text-xs font-semibold transition-colors',
            value.includes(i)
              ? 'border-primary bg-primary/20 text-primary'
              : 'border-border text-muted-foreground hover:border-primary/40',
          )}
        >
          {t(key)}
        </button>
      ))}
    </div>
  )
}

// ── Day-of-month picker ──────────────────────────────────────────────────────

function DayOfMonthPicker({
  value,
  onChange,
}: {
  value: number | 'last'
  onChange: (day: number | 'last') => void
}) {
  const t = useTranslations('scheduledTasks')
  return (
    <div className="flex flex-col gap-1.5">
      <div className="grid grid-cols-7 gap-1">
        {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => (
          <button
            key={d}
            type="button"
            onClick={() => onChange(d)}
            className={cn(
              'flex h-7 items-center justify-center rounded border text-[10px] font-semibold transition-colors',
              value === d
                ? 'border-primary bg-primary/20 text-primary'
                : 'border-border text-muted-foreground hover:border-primary/40',
            )}
          >
            {d}
          </button>
        ))}
        <button
          type="button"
          onClick={() => onChange('last')}
          className={cn(
            'col-span-3 flex items-center justify-center gap-1 rounded border py-1 text-[10px] font-semibold transition-colors',
            value === 'last'
              ? 'border-primary bg-primary/20 text-primary'
              : 'border-dashed border-primary/30 text-primary/60 hover:border-primary/50 hover:text-primary',
          )}
        >
          📅 {t('lastDayOfMonth')}
        </button>
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={`fill-${i}`} />
        ))}
      </div>
      <p className="text-[10px] text-muted-foreground">{t('dayOfMonthHint')}</p>
    </div>
  )
}

// ── Interval input ───────────────────────────────────────────────────────────

function IntervalInput({
  value,
  onChange,
}: {
  value: IntervalSchedule
  onChange: (s: IntervalSchedule) => void
}) {
  const t = useTranslations('scheduledTasks')
  return (
    <div className="flex items-center gap-2">
      <span className="text-sm text-muted-foreground">{t('intervalPrefix')}</span>
      <Input
        type="number"
        min={1}
        value={value.value}
        onChange={(e) => {
          const n = parseInt(e.target.value, 10)
          if (n > 0) onChange({ ...value, value: n })
        }}
        className="w-16 text-center"
      />
      <select
        value={value.unit}
        onChange={(e) => onChange({ ...value, unit: e.target.value as IntervalSchedule['unit'] })}
        className="rounded-md border border-border bg-input px-2 py-1.5 text-sm"
      >
        <option value="minutes">{t('unitMinutes')}</option>
        <option value="hours">{t('unitHours')}</option>
        <option value="days">{t('unitDays')}</option>
      </select>
    </div>
  )
}

// ── ScheduleEditor (orchestrator) ────────────────────────────────────────────

interface ScheduleEditorProps {
  value: ScheduleEditorValue
  onChange: (value: ScheduleEditorValue) => void
}

function scheduleToFreqKind(s: ScheduleState): FreqKind {
  if (s.kind === 'unsupported_cron') return 'daily'
  return s.kind
}

function defaultForKind(kind: FreqKind, timezone: string): ScheduleState {
  switch (kind) {
    case 'daily':
      return { kind: 'daily', hour: 9, minute: 0 }
    case 'weekly':
      return { kind: 'weekly', days: [1, 2, 3, 4, 5], hour: 9, minute: 0 }
    case 'monthly':
      return { kind: 'monthly', day: 1, hour: 9, minute: 0 }
    case 'interval':
      return { kind: 'interval', value: 1, unit: 'hours' }
    case 'once': {
      // Use task timezone so localDatetimeToUTC interprets this correctly.
      // toISOString() gives a UTC string; treating it as local would be wrong
      // by the full timezone offset.
      const tomorrow = new Date(Date.now() + 86400_000)
      const runAt = tomorrow
        .toLocaleString('sv', { timeZone: timezone })
        .replace(' ', 'T')
        .slice(0, 16)
      return { kind: 'once', runAt }
    }
  }
}

export function ScheduleEditor({ value, onChange }: ScheduleEditorProps) {
  const t = useTranslations('scheduledTasks')
  const s = value.schedule
  const isLegacy = s.kind === 'unsupported_cron'

  function setSchedule(schedule: ScheduleState) {
    onChange({ ...value, schedule })
  }

  function handleFreqChange(kind: FreqKind) {
    setSchedule(defaultForKind(kind, value.timezone))
  }

  if (isLegacy) {
    return (
      <div className="flex flex-col gap-3">
        <div className="rounded-md border border-warning-border bg-warning-surface px-3 py-2 text-xs text-warning-fg">
          ⚠ {t('legacyCron')}
          <code className="ml-1 font-mono">{s.cronExpr}</code>
        </div>
        <button
          type="button"
          onClick={() => setSchedule({ kind: 'daily', hour: 9, minute: 0 })}
          className="self-start rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground"
        >
          {t('switchToVisual')}
        </button>
      </div>
    )
  }

  const freqKind = scheduleToFreqKind(s)

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <Label className="text-xs uppercase tracking-wide text-muted-foreground">
          {t('frequency')}
        </Label>
        <FrequencyPills value={freqKind} onChange={handleFreqChange} />
      </div>

      <hr className="border-border" />

      {(s.kind === 'daily' || s.kind === 'weekly' || s.kind === 'monthly') && (
        <>
          {s.kind === 'weekly' && (
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs uppercase tracking-wide text-muted-foreground">
                {t('runDays')}
              </Label>
              <WeekdayPicker
                value={(s as WeeklySchedule).days}
                onChange={(days) => setSchedule({ ...(s as WeeklySchedule), days })}
              />
            </div>
          )}
          {s.kind === 'monthly' && (
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs uppercase tracking-wide text-muted-foreground">
                {t('dayOfMonth')}
              </Label>
              <DayOfMonthPicker
                value={(s as MonthlySchedule).day}
                onChange={(day) => setSchedule({ ...(s as MonthlySchedule), day })}
              />
            </div>
          )}
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs uppercase tracking-wide text-muted-foreground">
              {t('runTime')}
            </Label>
            <div className="flex items-center gap-2">
              <TimeInput
                hour={(s as DailySchedule).hour}
                minute={(s as DailySchedule).minute}
                onChange={(hour, minute) => setSchedule({ ...s, hour, minute } as ScheduleState)}
              />
              <TimezoneInput
                value={value.timezone}
                onChange={(tz) => onChange({ ...value, timezone: tz })}
              />
            </div>
          </div>
        </>
      )}

      {s.kind === 'interval' && <IntervalInput value={s} onChange={(next) => setSchedule(next)} />}

      {s.kind === 'once' && (
        <div className="flex flex-col gap-1.5">
          <Label className="text-xs uppercase tracking-wide text-muted-foreground">
            {t('runTime')}
          </Label>
          <div className="flex items-center gap-2">
            <Input
              type="datetime-local"
              value={s.runAt}
              onChange={(e) => setSchedule({ kind: 'once', runAt: e.target.value })}
              className="max-w-[220px]"
            />
            <TimezoneInput
              value={value.timezone}
              onChange={(tz) => onChange({ ...value, timezone: tz })}
            />
          </div>
        </div>
      )}

      {s.kind !== 'once' && (
        <EndDateInput value={value.endAt} onChange={(endAt) => onChange({ ...value, endAt })} />
      )}
    </div>
  )
}
