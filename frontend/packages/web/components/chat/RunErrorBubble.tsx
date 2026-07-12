'use client'

import { useTranslations } from 'next-intl'
import { AlertCircle } from 'lucide-react'
import type { ErrorEventData } from '@cubeplex/core'

export function RunErrorBubble({ data }: { data: ErrorEventData }) {
  const t = useTranslations('runError')
  const params = (data.params ?? {}) as Record<string, string | number>
  // next-intl in the default provider config doesn't throw on a missing key
  // — it logs through onError and returns the key string. Detect that the
  // returned value equals the input key (or a `runError.<code>` namespace
  // form) and fall back to the backend's English `message` instead.
  let localized: string
  try {
    const tAny = t as unknown as (key: string, params?: Record<string, string | number>) => string
    localized = tAny(data.error_code, params)
  } catch {
    localized = data.message
  }
  if (localized === data.error_code || localized === `runError.${data.error_code}`) {
    localized = data.message
  }
  return (
    <div
      role="alert"
      className="border border-danger-border bg-danger-surface text-danger-fg rounded px-3 py-2 flex items-start gap-2"
    >
      <AlertCircle className="size-4 shrink-0 mt-0.5" />
      <span>{localized}</span>
    </div>
  )
}
