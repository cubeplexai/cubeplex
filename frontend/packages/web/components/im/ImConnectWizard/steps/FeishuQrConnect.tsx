'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { ExternalLink, Loader2, RefreshCw } from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import { toast } from 'sonner'

import {
  wsCancelFeishuQr,
  wsGetFeishuQrStatus,
  wsStartFeishuQr,
  type ApiClient,
  type FeishuQrAttempt,
} from '@cubeplex/core'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'

type DynamicT = (key: string, values?: Record<string, string | number>) => string

interface Props {
  client: ApiClient
  wsId: string
  onConnected: () => void
}

const ACTIVE_STATES = new Set([
  'starting',
  'qr_ready',
  'polling',
  'slow_down',
  'domain_switched',
  'connecting',
])

export function FeishuQrConnect({ client, wsId, onConnected }: Props): React.ReactElement {
  const t = useTranslations() as unknown as DynamicT
  const [domain, setDomain] = useState<'feishu' | 'lark'>('feishu')
  const [attempt, setAttempt] = useState<FeishuQrAttempt | null>(null)
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)
  const [nowMs, setNowMs] = useState<number>(() => Date.now())

  const activeAttemptIdRef = useRef<string | null>(null)
  const mountedRef = useRef(true)

  const startQr = useCallback(
    async (targetDomain: 'feishu' | 'lark') => {
      const prevId = activeAttemptIdRef.current
      if (prevId) {
        activeAttemptIdRef.current = null
        void wsCancelFeishuQr(client, wsId, prevId).catch(() => {})
      }
      setStarting(true)
      setStartError(null)
      setAttempt(null)
      try {
        const res = await wsStartFeishuQr(client, wsId, targetDomain)
        if (!mountedRef.current) {
          void wsCancelFeishuQr(client, wsId, res.attempt_id).catch(() => {})
          return
        }
        activeAttemptIdRef.current = res.attempt_id
        setAttempt(res)
      } catch (err) {
        if (!mountedRef.current) return
        setStartError(err instanceof Error ? err.message : 'Failed to start QR login')
      } finally {
        if (mountedRef.current) setStarting(false)
      }
    },
    [client, wsId],
  )

  useEffect(() => {
    mountedRef.current = true
    const initTimer = window.setTimeout(() => {
      void startQr(domain)
    }, 0)
    return () => {
      window.clearTimeout(initTimer)
      mountedRef.current = false
      const aid = activeAttemptIdRef.current
      if (aid) {
        activeAttemptIdRef.current = null
        void wsCancelFeishuQr(client, wsId, aid).catch(() => {})
      }
    }
  }, [client, wsId, domain, startQr])

  // Poll QR status while attempt is active
  useEffect(() => {
    if (!attempt || !ACTIVE_STATES.has(attempt.state)) return
    const delayMs = attempt.state === 'slow_down' ? 4000 : 2000
    const timer = window.setTimeout(async () => {
      try {
        const next = await wsGetFeishuQrStatus(client, wsId, attempt.attempt_id)
        if (!mountedRef.current || activeAttemptIdRef.current !== attempt.attempt_id) return
        setAttempt(next)
        if (next.state === 'succeeded') {
          activeAttemptIdRef.current = null
          toast.success(t('im.success.toast.connected'))
          onConnected()
        }
      } catch {
        // Transient poll error; next render/refresh can retry
      }
    }, delayMs)
    return () => window.clearTimeout(timer)
  }, [attempt, client, wsId, onConnected, t])

  // Countdown ticker
  useEffect(() => {
    if (!attempt?.expires_at || !ACTIVE_STATES.has(attempt.state)) return
    const interval = window.setInterval(() => {
      setNowMs(Date.now())
    }, 1000)
    return () => window.clearInterval(interval)
  }, [attempt?.expires_at, attempt?.state])

  const remainingSeconds =
    attempt?.expires_at && ACTIVE_STATES.has(attempt.state)
      ? Math.max(0, Math.ceil((Date.parse(attempt.expires_at) - nowMs) / 1000))
      : null

  const isExpired = attempt?.state === 'expired'
  const isError = attempt?.state === 'error' || !!startError
  const isConnecting = attempt?.state === 'connecting'

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">{t('im.wizard.feishu.qr.subtitle')}</p>

      <div className="flex gap-2">
        <Button
          type="button"
          size="sm"
          variant={domain === 'feishu' ? 'default' : 'outline'}
          onClick={() => setDomain('feishu')}
          disabled={isConnecting}
        >
          {t('im.wizard.feishu.domain.feishu')}
        </Button>
        <Button
          type="button"
          size="sm"
          variant={domain === 'lark' ? 'default' : 'outline'}
          onClick={() => setDomain('lark')}
          disabled={isConnecting}
        >
          {t('im.wizard.feishu.domain.lark')}
        </Button>
      </div>

      {(isError || isExpired) && (
        <Alert variant="destructive">
          <AlertDescription>
            {isExpired
              ? t('im.wizard.feishu.qr.expired')
              : startError || attempt?.error_message || 'Registration failed'}
          </AlertDescription>
        </Alert>
      )}

      <div className="flex flex-col items-center justify-center gap-3 rounded-lg border bg-muted/20 p-6">
        {starting || attempt?.state === 'starting' ? (
          <div className="flex h-48 w-48 flex-col items-center justify-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-6 w-6 animate-spin" />
            <span>{t('im.wizard.feishu.qr.loading')}</span>
          </div>
        ) : isConnecting ? (
          <div className="flex h-48 w-48 flex-col items-center justify-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-6 w-6 animate-spin" />
            <span>{t('im.wizard.feishu.qr.connecting')}</span>
          </div>
        ) : attempt?.qr_url ? (
          <>
            <div className="rounded-lg border bg-white p-3 shadow-xs">
              <QRCodeSVG value={attempt.qr_url} size={184} level="M" />
            </div>
            <div className="flex flex-col items-center gap-1 text-center">
              <span className="text-xs font-medium">{t('im.wizard.feishu.qr.waiting')}</span>
              {remainingSeconds !== null && (
                <span className="text-[11px] text-muted-foreground">
                  {t('im.wizard.feishu.qr.expiresIn', { seconds: remainingSeconds })}
                </span>
              )}
              <a
                href={attempt.qr_url}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-1 inline-flex items-center gap-1 text-xs text-primary hover:underline"
              >
                {t('im.wizard.feishu.qr.openLink')}
                <ExternalLink className="h-3 w-3" />
              </a>
            </div>
          </>
        ) : (
          <Button type="button" variant="outline" size="sm" onClick={() => void startQr(domain)}>
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            {t('im.wizard.feishu.qr.refresh')}
          </Button>
        )}
      </div>

      {(isExpired || isError) && (
        <div className="flex justify-end">
          <Button type="button" variant="outline" size="sm" onClick={() => void startQr(domain)}>
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            {t('im.wizard.feishu.qr.refresh')}
          </Button>
        </div>
      )}
    </div>
  )
}
