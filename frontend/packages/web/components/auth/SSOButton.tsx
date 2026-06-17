'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { createApiClient, initiateSsoLogin } from '@cubebox/core'

interface SSOButtonProps {
  /** When provided, click goes straight to SSO for this org — no slug input. */
  orgSlug?: string
  /** In single-tenant mode, no slug input is needed. */
  singleTenant?: boolean
}

export function SSOButton({ orgSlug, singleTenant }: SSOButtonProps) {
  const t = useTranslations('auth')
  const [showInput, setShowInput] = useState(false)
  const [slug, setSlug] = useState(orgSlug ?? '')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const startSSO = async (targetSlug?: string) => {
    setLoading(true)
    setError(null)
    try {
      const client = createApiClient('')
      const { redirect_url } = await initiateSsoLogin(client, targetSlug)
      window.location.href = redirect_url
    } catch (err) {
      setError((err as Error).message)
      setLoading(false)
    }
  }

  const onClick = () => {
    if (orgSlug || singleTenant) {
      startSSO(orgSlug)
    } else {
      setShowInput(true)
    }
  }

  if (showInput) {
    return (
      <div className="space-y-2">
        <input
          type="text"
          placeholder={t('orgSlugPlaceholder')}
          aria-label={t('orgSlugPlaceholder')}
          autoComplete="off"
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          value={slug}
          onChange={(e) => setSlug(e.target.value)}
          autoFocus
        />
        {error && <div className="text-sm text-destructive">{error}</div>}
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => startSSO(slug.trim())}
            disabled={loading || !slug.trim()}
            className="flex-1 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-background disabled:opacity-50"
          >
            {loading ? t('redirecting') : t('continue')}
          </button>
          <button
            type="button"
            onClick={() => {
              setShowInput(false)
              setError(null)
            }}
            className="rounded-md border border-border px-3 py-2 text-sm hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-background"
          >
            {t('back')}
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={onClick}
        disabled={loading}
        className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm font-medium hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-background disabled:opacity-50"
      >
        {loading ? t('redirecting') : t('loginWithSSO')}
      </button>
      {error && <div className="text-sm text-destructive">{error}</div>}
    </div>
  )
}
