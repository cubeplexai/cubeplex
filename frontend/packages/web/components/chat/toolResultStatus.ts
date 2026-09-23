export function toolResultIsRunning(result: { details?: unknown } | null | undefined): boolean {
  if (result == null) return false
  const details = result.details
  return (
    typeof details === 'object' &&
    details !== null &&
    'status' in details &&
    (details as { status?: unknown }).status === 'running'
  )
}
