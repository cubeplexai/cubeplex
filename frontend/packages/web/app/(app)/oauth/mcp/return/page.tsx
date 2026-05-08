'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { useMcpStore, useWorkspaceMcpStore } from '@cubebox/core'
import { CheckCircle2, AlertTriangle } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'

const OAUTH_ORIGIN_KEY = 'mcp_oauth_origin'
const SUCCESS_REDIRECT_DELAY_MS = 1500

type Status = 'ok' | 'error' | 'unknown'

function readOriginAndClear(): string | null {
  if (typeof window === 'undefined') return null
  try {
    const value = window.sessionStorage.getItem(OAUTH_ORIGIN_KEY)
    if (value) {
      window.sessionStorage.removeItem(OAUTH_ORIGIN_KEY)
    }
    return value
  } catch {
    return null
  }
}

function isSafeReturnPath(value: string | null): value is string {
  if (!value) return false
  // Only allow same-origin pathname+query — never absolute URLs.
  return value.startsWith('/') && !value.startsWith('//')
}

type ReasonKey =
  | 'errorStateInvalid'
  | 'errorPkceMissing'
  | 'errorTicketMissing'
  | 'errorTicketExpired'
  | 'errorTokenExchange'
  | 'errorInvalidServerState'
  | 'errorInternal'

function reasonKey(reason: string | null): ReasonKey {
  switch (reason) {
    case 'state_invalid':
      return 'errorStateInvalid'
    case 'pkce_missing':
      return 'errorPkceMissing'
    case 'callback_ticket_missing':
      return 'errorTicketMissing'
    case 'callback_ticket_expired':
      return 'errorTicketExpired'
    case 'token_exchange_failed':
      return 'errorTokenExchange'
    case 'invalid_server_state':
      return 'errorInvalidServerState'
    case 'internal_error':
    default:
      return 'errorInternal'
  }
}

export default function MCPOAuthReturnPage() {
  const t = useTranslations('mcpOAuthReturn')
  const router = useRouter()
  const searchParams = useSearchParams()

  const rawStatus = searchParams.get('status')
  const reason = searchParams.get('reason')
  const status: Status = rawStatus === 'ok' ? 'ok' : rawStatus === 'error' ? 'error' : 'unknown'

  const clearAdmin = useMcpStore((s) => s.clearPendingOAuth)
  const clearWorkspace = useWorkspaceMcpStore((s) => s.clearPendingOAuth)

  // Read sessionStorage exactly once on mount and stash for both render + redirect.
  const consumedRef = useRef(false)
  const [origin, setOrigin] = useState<string | null>(null)

  useEffect(() => {
    if (consumedRef.current) return
    consumedRef.current = true
    const value = readOriginAndClear()
    setOrigin(isSafeReturnPath(value) ? value : null)
    // Always clear both stores' pendingOAuthInstallId on mount, regardless of outcome.
    clearAdmin()
    clearWorkspace()
  }, [clearAdmin, clearWorkspace])

  const fallbackHref = useMemo(() => '/', [])
  const returnHref = origin ?? fallbackHref

  useEffect(() => {
    if (status !== 'ok') return
    const id = window.setTimeout(() => {
      router.replace(returnHref)
    }, SUCCESS_REDIRECT_DELAY_MS)
    return () => window.clearTimeout(id)
  }, [status, returnHref, router])

  return (
    <div className="flex min-h-full items-center justify-center p-6">
      <Card className="flex w-full max-w-md flex-col gap-4 p-6">
        {status === 'ok' ? (
          <Alert>
            <CheckCircle2 className="size-4" aria-hidden />
            <AlertTitle>{t('successTitle')}</AlertTitle>
            <AlertDescription>{t('successBody')}</AlertDescription>
          </Alert>
        ) : status === 'error' ? (
          <>
            <Alert variant="destructive">
              <AlertTriangle className="size-4" aria-hidden />
              <AlertTitle>{t('errorTitle')}</AlertTitle>
              <AlertDescription>{t(reasonKey(reason))}</AlertDescription>
            </Alert>
            <div className="flex justify-end">
              <Button onClick={() => router.replace(returnHref)}>
                {origin ? t('returnButton') : t('fallbackHomeButton')}
              </Button>
            </div>
          </>
        ) : (
          <>
            <Alert>
              <AlertTitle>{t('errorTitle')}</AlertTitle>
              <AlertDescription>{t('missingParams')}</AlertDescription>
            </Alert>
            <div className="flex justify-end">
              <Button onClick={() => router.replace(fallbackHref)}>
                {t('fallbackHomeButton')}
              </Button>
            </div>
          </>
        )}
      </Card>
    </div>
  )
}
