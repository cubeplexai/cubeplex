'use client'

import { useEffect, useRef, useState } from 'react'
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

const FREQ_LABELS: { kind: FreqKind; label: string }[] = [
  { kind: 'daily', label: '每天' },
  { kind: 'weekly', label: '每周' },
  { kind: 'monthly', label: '每月' },
  { kind: 'interval', label: '每隔…' },
  { kind: 'once', label: '一次' },
]

function FrequencyPills({
  value,
  onChange,
}: {
  value: FreqKind
  onChange: (kind: FreqKind) => void
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {FREQ_LABELS.map(({ kind, label }) => (
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
          {label}
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
      {error && <span className="text-xs text-destructive">无效时区</span>}
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
          截止日期 <span className="text-primary text-[10px]">可选</span>
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
          <span className="text-xs text-muted-foreground">到期后自动停止</span>
        </div>
      )}
      {!enabled && <p className="text-xs italic text-muted-foreground">未设置 — 永久运行</p>}
    </div>
  )
}

// ── Weekday picker ───────────────────────────────────────────────────────────

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']

function WeekdayPicker({
  value,
  onChange,
}: {
  value: number[]
  onChange: (days: number[]) => void
}) {
  function toggle(day: number) {
    const next = value.includes(day) ? value.filter((d) => d !== day) : [...value, day]
    if (next.length > 0) onChange(next)
  }
  return (
    <div className="flex gap-1">
      {WEEKDAYS.map((label, i) => (
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
          {label}
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
          📅 月末最后一天
        </button>
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={`fill-${i}`} />
        ))}
      </div>
      <p className="text-[10px] text-muted-foreground">
        1–28 避免短月 skip；月末选项自动适配每月实际天数
      </p>
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
  return (
    <div className="flex items-center gap-2">
      <span className="text-sm text-muted-foreground">每隔</span>
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
        <option value="minutes">分钟</option>
        <option value="hours">小时</option>
        <option value="days">天</option>
      </select>
      <span className="text-sm text-muted-foreground">执行一次</span>
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

function defaultForKind(kind: FreqKind): ScheduleState {
  switch (kind) {
    case 'daily':
      return { kind: 'daily', hour: 9, minute: 0 }
    case 'weekly':
      return { kind: 'weekly', days: [1, 2, 3, 4, 5], hour: 9, minute: 0 }
    case 'monthly':
      return { kind: 'monthly', day: 1, hour: 9, minute: 0 }
    case 'interval':
      return { kind: 'interval', value: 1, unit: 'hours' }
    case 'once':
      return {
        kind: 'once',
        runAt: new Date(Date.now() + 86400_000).toISOString().slice(0, 16),
      }
  }
}

export function ScheduleEditor({ value, onChange }: ScheduleEditorProps) {
  const s = value.schedule
  const isLegacy = s.kind === 'unsupported_cron'

  function setSchedule(schedule: ScheduleState) {
    onChange({ ...value, schedule })
  }

  function handleFreqChange(kind: FreqKind) {
    setSchedule(defaultForKind(kind))
  }

  if (isLegacy) {
    return (
      <div className="flex flex-col gap-3">
        <div className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-600 dark:text-amber-400">
          ⚠ 当前使用了自定义 cron 表达式：
          <code className="ml-1 font-mono">{s.cronExpr}</code>
        </div>
        <button
          type="button"
          onClick={() => setSchedule({ kind: 'daily', hour: 9, minute: 0 })}
          className="self-start rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground"
        >
          切换到可视化配置（将清除当前 cron）
        </button>
      </div>
    )
  }

  const freqKind = scheduleToFreqKind(s)

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <Label className="text-xs uppercase tracking-wide text-muted-foreground">频率</Label>
        <FrequencyPills value={freqKind} onChange={handleFreqChange} />
      </div>

      <hr className="border-border" />

      {(s.kind === 'daily' || s.kind === 'weekly' || s.kind === 'monthly') && (
        <>
          {s.kind === 'weekly' && (
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs uppercase tracking-wide text-muted-foreground">
                运行日（可多选）
              </Label>
              <WeekdayPicker
                value={(s as WeeklySchedule).days}
                onChange={(days) => setSchedule({ ...(s as WeeklySchedule), days })}
              />
            </div>
          )}
          {s.kind === 'monthly' && (
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs uppercase tracking-wide text-muted-foreground">日期</Label>
              <DayOfMonthPicker
                value={(s as MonthlySchedule).day}
                onChange={(day) => setSchedule({ ...(s as MonthlySchedule), day })}
              />
            </div>
          )}
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs uppercase tracking-wide text-muted-foreground">
              运行时间
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
          <Label className="text-xs uppercase tracking-wide text-muted-foreground">运行时间</Label>
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
