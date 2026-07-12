'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { createApiClient, initiateSsoLogin } from '@cubeplex/core'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

interface SSOButtonProps {
  /** When provided, click goes straight to SSO for this org, no slug input. */
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
      <div className="flex flex-col gap-2">
        <Input
          type="text"
          placeholder={t('orgSlugPlaceholder')}
          aria-label={t('orgSlugPlaceholder')}
          autoComplete="off"
          value={slug}
          onChange={(e) => setSlug(e.target.value)}
          autoFocus
        />
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <div className="flex gap-2">
          <Button
            type="button"
            size="lg"
            onClick={() => startSSO(slug.trim())}
            disabled={loading || !slug.trim()}
            className="flex-1"
          >
            {loading ? t('redirecting') : t('continue')}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="lg"
            onClick={() => {
              setShowInput(false)
              setError(null)
            }}
          >
            {t('back')}
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <Button
        type="button"
        variant="outline"
        size="lg"
        onClick={onClick}
        disabled={loading}
        className="w-full"
      >
        {loading ? t('redirecting') : t('loginWithSSO')}
      </Button>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
    </div>
  )
}
