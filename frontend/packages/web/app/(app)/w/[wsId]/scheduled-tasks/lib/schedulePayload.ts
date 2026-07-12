// Pure, side-effect-free helpers that translate between the visual schedule
// editor's UI state and the API's schedule fields.

import type { ScheduledTaskCreate, ScheduledTaskOut } from '@cubeplex/core'

// ── UI state types ────────────────────────────────────────────────────────────

export interface DailySchedule {
  kind: 'daily'
  hour: number // 0–23
  minute: number // 0–59
}

export interface WeeklySchedule {
  kind: 'weekly'
  days: number[] // croniter convention: 0=Sun 1=Mon … 6=Sat; at least one
  hour: number
  minute: number
}

export interface MonthlySchedule {
  kind: 'monthly'
  day: number | 'last' // 1–28 or 'last' (croniter L)
  hour: number
  minute: number
}

export interface IntervalSchedule {
  kind: 'interval'
  value: number // positive integer
  unit: 'minutes' | 'hours' | 'days'
}

export interface OnceSchedule {
  kind: 'once'
  runAt: string // datetime-local value "YYYY-MM-DDTHH:mm"
}

export interface UnsupportedCronSchedule {
  kind: 'unsupported_cron'
  cronExpr: string
}

export type ScheduleState =
  | DailySchedule
  | WeeklySchedule
  | MonthlySchedule
  | IntervalSchedule
  | OnceSchedule
  | UnsupportedCronSchedule

export interface ScheduleEditorValue {
  schedule: ScheduleState
  timezone: string
  /** YYYY-MM-DD or null (null = no deadline / run forever) */
  endAt: string | null
}

// ── Default value for new tasks ───────────────────────────────────────────────

export function defaultScheduleEditorValue(tz: string): ScheduleEditorValue {
  return {
    schedule: { kind: 'daily', hour: 9, minute: 0 },
    timezone: tz,
    endAt: null,
  }
}

// ── Timezone-aware datetime helpers ─────────────────────────────────────────

/**
 * Convert a local datetime string ("YYYY-MM-DDTHH:mm") to a UTC ISO string,
 * interpreting the input as being in the given IANA timezone — NOT the browser's
 * local timezone. `new Date(str)` without a suffix uses the browser's TZ and
 * would produce the wrong UTC value whenever browser TZ ≠ task TZ.
 *
 * Algorithm: create a UTC "guess" treating the string as UTC, then measure how
 * far it is from the desired local time via Intl, and apply the delta.
 * One iteration is sufficient for standard offsets; DST edge cases (the
 * "spring forward" hour) may be off by 1h — acceptable for v1.
 */
export function localDatetimeToUTC(datetimeLocal: string, timezone: string): string {
  const guess = new Date(datetimeLocal + ':00Z')
  const tzRepr = guess.toLocaleString('sv', { timeZone: timezone }).replace(' ', 'T')
  const delta = guess.getTime() - new Date(tzRepr + 'Z').getTime()
  return new Date(guess.getTime() + delta).toISOString()
}

/**
 * Return the UTC instant at which the given date ends in the task timezone.
 * Defined as: start of the day AFTER `dateStr` in the task timezone.
 * Semantics: task fires on `dateStr` in local time, but not after.
 *
 * Example: dateStr="2026-06-01", tz="Asia/Shanghai" (UTC+8)
 *   → 2026-06-02T00:00:00+08:00 → 2026-06-01T16:00:00Z
 * A daily-09:00-CST task fires at 01:00Z; last fire Jun 1 (01:00Z < 16:00Z ✓),
 * first skip Jun 2 (01:00Z Jun 2 > 16:00Z Jun 1 ✓).
 */
export function endOfDayUTC(dateStr: string, timezone: string): string {
  const [y, mo, d] = dateStr.split('-').map(Number)
  // Use Date.UTC for rollover: d+1 on a month-end (e.g. Jan 31 → Feb 1).
  // String arithmetic ("2026-01-32") produces Invalid Date and throws.
  const nextDayDate = new Date(Date.UTC(y, mo - 1, d + 1))
  const pad = (n: number) => String(n).padStart(2, '0')
  const nextDay = `${nextDayDate.getUTCFullYear()}-${pad(nextDayDate.getUTCMonth() + 1)}-${pad(nextDayDate.getUTCDate())}T00:00`
  return localDatetimeToUTC(nextDay, timezone)
}

// ── Build API payload from UI state ──────────────────────────────────────────

type SchedulePayload = Pick<
  ScheduledTaskCreate,
  'schedule_kind' | 'cron_expr' | 'interval_seconds' | 'run_at' | 'timezone' | 'end_at'
>

/**
 * Convert UI state to the backend schedule fields.
 * Cron times are kept in local time; the backend uses task.timezone for evaluation.
 * end_at: interpreted in the task timezone via endOfDayUTC (not UTC midnight).
 * Null (no deadline) is sent as explicit JSON null so PATCH can clear an existing deadline
 * via Pydantic's model_fields_set detection. The `once` kind omits end_at entirely.
 */
export function buildSchedulePayload(v: ScheduleEditorValue): SchedulePayload {
  const endAt: string | null = v.endAt ? endOfDayUTC(v.endAt, v.timezone) : null
  const s = v.schedule

  switch (s.kind) {
    case 'daily':
      return {
        schedule_kind: 'cron',
        cron_expr: `${s.minute} ${s.hour} * * *`,
        timezone: v.timezone,
        end_at: endAt,
      }
    case 'weekly': {
      const dow = [...s.days].sort((a, b) => a - b).join(',')
      return {
        schedule_kind: 'cron',
        cron_expr: `${s.minute} ${s.hour} * * ${dow}`,
        timezone: v.timezone,
        end_at: endAt,
      }
    }
    case 'monthly': {
      const dom = s.day === 'last' ? 'L' : String(s.day)
      return {
        schedule_kind: 'cron',
        cron_expr: `${s.minute} ${s.hour} ${dom} * *`,
        timezone: v.timezone,
        end_at: endAt,
      }
    }
    case 'interval': {
      const unitSeconds: Record<IntervalSchedule['unit'], number> = {
        minutes: 60,
        hours: 3600,
        days: 86400,
      }
      return {
        schedule_kind: 'interval',
        interval_seconds: s.value * unitSeconds[s.unit],
        timezone: v.timezone,
        end_at: endAt,
      }
    }
    case 'once':
      return {
        schedule_kind: 'once',
        // Use localDatetimeToUTC, NOT new Date(s.runAt) — the latter uses the
        // browser's timezone, not the task timezone, producing wrong results
        // whenever the two differ.
        run_at: localDatetimeToUTC(s.runAt, v.timezone),
        timezone: v.timezone,
      }
    case 'unsupported_cron':
      return {
        schedule_kind: 'cron',
        cron_expr: s.cronExpr,
        timezone: v.timezone,
        end_at: endAt,
      }
  }
}

// ── Parse API task back to UI state (edit mode) ───────────────────────────────

/**
 * Parse a 5-field cron into a daily/weekly/monthly ScheduleState, or return
 * UnsupportedCronSchedule for any pattern we didn't generate ourselves.
 *
 * Pattern rules (all 5-field, fields: minute hour dom month dow):
 *   daily:   M H * * *
 *   weekly:  M H * * DOW   (DOW is not *)
 *   monthly: M H DOM * *   (DOM is not *)
 *   other:   unsupported_cron
 */
function parseCron(expr: string): ScheduleState {
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) return { kind: 'unsupported_cron', cronExpr: expr }

  const [rawMin, rawHour, dom, month, dow] = parts
  const minute = Number(rawMin)
  const hour = Number(rawHour)

  if (isNaN(minute) || isNaN(hour) || month !== '*') {
    return { kind: 'unsupported_cron', cronExpr: expr }
  }

  // daily: dom=* dow=*
  if (dom === '*' && dow === '*') {
    return { kind: 'daily', hour, minute }
  }

  // weekly: dom=* dow!=*
  if (dom === '*' && dow !== '*') {
    const days = dow.split(',').map(Number)
    if (days.some(isNaN)) return { kind: 'unsupported_cron', cronExpr: expr }
    return { kind: 'weekly', days, hour, minute }
  }

  // monthly: dom!=* dow=*
  if (dom !== '*' && dow === '*') {
    const day: number | 'last' = dom === 'L' ? 'last' : Number(dom)
    if (day !== 'last' && isNaN(day)) return { kind: 'unsupported_cron', cronExpr: expr }
    return { kind: 'monthly', day, hour, minute }
  }

  return { kind: 'unsupported_cron', cronExpr: expr }
}

function parseInterval(seconds: number): IntervalSchedule {
  if (seconds % 86400 === 0) return { kind: 'interval', value: seconds / 86400, unit: 'days' }
  if (seconds % 3600 === 0) return { kind: 'interval', value: seconds / 3600, unit: 'hours' }
  return { kind: 'interval', value: Math.round(seconds / 60), unit: 'minutes' }
}

export function parseSchedulePayload(task: ScheduledTaskOut): ScheduleEditorValue {
  // end_at was stored as "start of next day in task TZ" (via endOfDayUTC).
  // Reverse: convert to local date in task TZ, then subtract 1 day.
  let endAt: string | null = null
  if (task.end_at) {
    const nextDayLocal = new Date(task.end_at)
      .toLocaleString('sv', { timeZone: task.timezone })
      .slice(0, 10) // "YYYY-MM-DD" of next day in task TZ
    // Use UTC arithmetic — getDate/setDate use browser's local TZ and cause
    // an off-by-one for users in negative UTC offsets (Americas).
    const [ny, nm, nd] = nextDayLocal.split('-').map(Number)
    endAt = new Date(Date.UTC(ny, nm - 1, nd - 1)).toISOString().slice(0, 10)
  }

  let schedule: ScheduleState

  if (task.schedule_kind === 'cron' && task.cron_expr) {
    schedule = parseCron(task.cron_expr)
  } else if (task.schedule_kind === 'interval' && task.interval_seconds != null) {
    schedule = parseInterval(task.interval_seconds)
  } else if (task.schedule_kind === 'once' && task.run_at) {
    // Convert UTC ISO back to "YYYY-MM-DDTHH:mm" in the task's timezone.
    // task.run_at.slice(0,16) would give UTC wall-clock time, not local time.
    const localStr = new Date(task.run_at)
      .toLocaleString('sv', { timeZone: task.timezone })
      .replace(' ', 'T')
      .slice(0, 16)
    schedule = { kind: 'once', runAt: localStr }
  } else {
    schedule = { kind: 'unsupported_cron', cronExpr: task.cron_expr ?? '' }
  }

  return { schedule, timezone: task.timezone, endAt }
}
