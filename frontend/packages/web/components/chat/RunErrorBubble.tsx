'use client'

import { useTranslations } from 'next-intl'
import { AlertCircle } from 'lucide-react'
import type { ErrorEventData } from '@cubebox/core'

export function RunErrorBubble({ data }: { data: ErrorEventData }) {
  const t = useTranslations('runError')
  const params = (data.params ?? {}) as Record<string, string | number>
  // next-intl throws on a missing key; wrap in try/catch and fall back to the
  // backend's English message. Cast through unknown because next-intl's t()
  // types only accept statically-known keys; we need runtime flexibility here.
  let localized: string
  try {
    const tAny = t as unknown as (key: string, params?: Record<string, string | number>) => string
    localized = tAny(data.error_code, params)
  } catch {
    localized = data.message
  }
  return (
    <div
      role="alert"
      className="flex items-start gap-2 px-3 py-2.5 rounded-lg
      bg-destructive/10 border border-destructive/20 text-destructive text-sm"
    >
      <AlertCircle className="size-4 shrink-0 mt-0.5" />
      <span>{localized}</span>
    </div>
  )
}
