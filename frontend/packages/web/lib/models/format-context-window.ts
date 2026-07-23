/** Format a context window token count for compact UI (e.g. 128000 → "128k"). */
export function formatContextWindow(n: number | null | undefined): string | null {
  if (n == null || !Number.isFinite(n) || n < 0) return null
  if (n < 1000) return String(Math.round(n))
  if (n % 1000 === 0) return `${n / 1000}k`
  const k = n / 1000
  const rounded = Math.round(k * 10) / 10
  return `${rounded}k`
}
