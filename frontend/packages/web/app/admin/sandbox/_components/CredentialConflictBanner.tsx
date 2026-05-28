'use client'

import { AlertTriangle } from 'lucide-react'

interface Props {
  warnings: string[]
}

/**
 * Yellow conflict banner shown after a save when the new network policy
 * denies a host that an installed credential needs. Soft warning — the
 * policy still saves; the user just needs to know outbound calls to those
 * hosts will be blocked.
 */
export function CredentialConflictBanner({ warnings }: Props) {
  if (warnings.length === 0) return null
  return (
    <div
      role="status"
      data-testid="sandbox-policy-conflict-banner"
      className="flex items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-amber-900 shadow-sm dark:border-amber-400/40 dark:bg-amber-950/40 dark:text-amber-100"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600 dark:text-amber-300" />
      <div className="flex flex-col gap-1 text-xs leading-relaxed">
        <p className="font-medium text-amber-900 dark:text-amber-100">
          Network policy conflicts with installed credentials
        </p>
        <ul className="list-disc space-y-0.5 pl-4 text-amber-800/90 dark:text-amber-200/90">
          {warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      </div>
    </div>
  )
}
