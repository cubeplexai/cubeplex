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
      className="flex items-start gap-3 rounded-lg border border-warning-border bg-warning-surface px-4 py-3 text-warning-fg shadow-sm"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning-fg" />
      <div className="flex flex-col gap-1 text-xs leading-relaxed">
        <p className="font-medium text-warning-fg">
          Network policy conflicts with installed credentials
        </p>
        <ul className="list-disc space-y-0.5 pl-4 text-warning-fg/90">
          {warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      </div>
    </div>
  )
}
