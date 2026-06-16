import type { ScheduledTaskOut } from '@cubebox/core'

import { parseSchedulePayload } from './schedulePayload'

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

/**
 * Human-readable schedule for the list card and detail. Cron tasks are
 * reverse-mapped through the same parser the form uses, so users see the
 * friendly option they picked (e.g. "Daily at 09:00") instead of the raw
 * `0 9 * * *` expression. Only genuinely hand-written cron we can't recognise
 * falls back to the raw expression.
 */
export function formatScheduleSummary(task: ScheduledTaskOut): string {
  if (task.schedule_kind === 'cron') {
    const tz = task.timezone && task.timezone !== 'UTC' ? ` (${task.timezone})` : ''
    const { schedule } = parseSchedulePayload(task)
    switch (schedule.kind) {
      case 'daily':
        return `Daily at ${pad2(schedule.hour)}:${pad2(schedule.minute)}${tz}`
      case 'weekly': {
        const days = [...schedule.days]
          .sort((a, b) => a - b)
          .map((d) => WEEKDAYS[d] ?? d)
          .join(', ')
        return `Weekly · ${days} at ${pad2(schedule.hour)}:${pad2(schedule.minute)}${tz}`
      }
      case 'monthly': {
        const day = schedule.day === 'last' ? 'last day' : `day ${schedule.day}`
        return `Monthly · ${day} at ${pad2(schedule.hour)}:${pad2(schedule.minute)}${tz}`
      }
      default:
        return task.cron_expr ?? '—'
    }
  }
  if (task.schedule_kind === 'interval' && task.interval_seconds != null) {
    const secs = task.interval_seconds
    if (secs < 60) return `Every ${secs}s`
    if (secs < 3600) return `Every ${Math.round(secs / 60)} min`
    if (secs < 86400) {
      const h = secs / 3600
      return h === 1 ? 'Every hour' : `Every ${h} hours`
    }
    const d = secs / 86400
    return d === 1 ? 'Every day' : `Every ${d} days`
  }
  if (task.schedule_kind === 'once' && task.run_at) {
    return `Once at ${new Intl.DateTimeFormat('en', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(new Date(task.run_at))}`
  }
  return '—'
}

export function formatNextFire(task: ScheduledTaskOut): string {
  if (task.status === 'paused') return 'Paused'
  if (!task.next_fire_at) return 'No upcoming run'
  const now = Date.now()
  const diff = new Date(task.next_fire_at).getTime() - now
  const absDiff = Math.abs(diff)
  if (absDiff < 60_000) return 'In less than a minute'
  if (absDiff < 3_600_000) return `In ${Math.round(absDiff / 60_000)} min`
  if (absDiff < 86_400_000) return `In ${Math.round(absDiff / 3_600_000)}h`
  return `In ${Math.round(absDiff / 86_400_000)}d`
}
