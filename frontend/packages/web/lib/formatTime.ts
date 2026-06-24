// epochSeconds matches cubepi's Message.timestamp convention.

const MINUTE = 60
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR
const WEEK = 7 * DAY

export type RelativeTimeBucket =
  | { kind: 'justNow' }
  | { kind: 'minutes'; n: number }
  | { kind: 'hours'; n: number }
  | { kind: 'days'; n: number }
  | { kind: 'date'; date: Date }

export function bucketRelativeTime(
  epochSeconds: number | null | undefined,
): RelativeTimeBucket | null {
  if (epochSeconds == null) return null
  const now = Math.floor(Date.now() / 1000)
  const diff = Math.max(0, now - epochSeconds)
  if (diff < MINUTE) return { kind: 'justNow' }
  if (diff < HOUR) return { kind: 'minutes', n: Math.floor(diff / MINUTE) }
  if (diff < DAY) return { kind: 'hours', n: Math.floor(diff / HOUR) }
  if (diff < WEEK) return { kind: 'days', n: Math.floor(diff / DAY) }
  return { kind: 'date', date: new Date(epochSeconds * 1000) }
}

export function formatAbsoluteTime(
  epochSeconds: number | null | undefined,
  locale?: string,
): string {
  if (epochSeconds == null) return ''
  const date = new Date(epochSeconds * 1000)
  return date.toLocaleString(locale, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}
