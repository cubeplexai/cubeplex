'use client'

/**
 * Pure presentation layer for the auth action band.
 *
 * Given a pre-computed {@link AuthBandState} and a small set of action
 * callbacks, renders the colored banner with icon/title/body/buttons. No
 * scope-specific branching, no API calls — wrappers (Admin/Ws bands) bind
 * state derivation and write APIs.
 */

import { useState, type ReactNode } from 'react'
import { useTranslations } from 'next-intl'
import { AlertTriangle, CheckCircle2, Clock, MoreHorizontal, XCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'

import type { AuthBandState, AuthReason } from './effectiveAuthState'

type BandT = ReturnType<typeof useTranslations<'mcp.auth'>>

export interface DisconnectOption {
  scope: 'org' | 'workspace' | 'user'
  label: string
  onClick: () => void
}

export interface AuthBandFrameProps {
  state: AuthBandState
  /** Auth method drives the needs-action variant (button vs token form). */
  authMethod?: 'oauth' | 'static'
  /** Provider label injected into the OAuth connect button copy. */
  providerLabel?: string
  /** Primary action for the OAuth needs-action variant. */
  onConnect?: () => void
  /** Save handler for the static-token needs-action variant. */
  onSaveStaticToken?: (token: string) => void
  /** Menu items shown in the ready/with_credential disconnect dropdown. */
  disconnectOptions?: DisconnectOption[]
  /** Retry button for the error state. Resets the parent's error. */
  onRetryError?: () => void
  /** Optional reason string shown in the error banner body. */
  errorMessage?: string
  /** Renders the in-flight banner regardless of state.kind when true. */
  inFlight?: boolean
}

export function AuthBandFrame(props: AuthBandFrameProps) {
  const t = useTranslations('mcp.auth')
  const { state } = props

  if (state.kind === 'hidden') return null

  if (state.kind === 'oauth-in-flight' || props.inFlight) {
    return (
      <Banner color="amber" icon={<Clock className="size-4" />}>
        <div className="flex-1">
          <p className="font-medium">{t('bandTitleInFlight')}</p>
        </div>
      </Banner>
    )
  }

  if (state.kind === 'error' || props.errorMessage) {
    const reason = props.errorMessage ?? (state.kind === 'error' ? state.reason : undefined)
    return (
      <Banner color="rose" icon={<XCircle className="size-4" />}>
        <div className="flex-1">
          <p className="font-medium">{t('bandTitleError')}</p>
          <p className="text-xs text-muted-foreground">{errorReasonCopy(t, reason)}</p>
        </div>
        {props.onRetryError && (
          <Button size="sm" variant="outline" onClick={props.onRetryError}>
            {t('retryButton')}
          </Button>
        )}
      </Banner>
    )
  }

  if (state.kind === 'ready') {
    return <ReadyBand state={state} t={t} disconnectOptions={props.disconnectOptions ?? []} />
  }

  if (state.kind === 'awaiting-others') {
    return <AwaitingBand state={state} t={t} />
  }

  if (state.kind === 'needs-action') {
    if (props.authMethod === 'static') {
      return <StaticTokenForm t={t} reason={state.reason} onSave={props.onSaveStaticToken} />
    }
    return (
      <Banner color="amber" icon={<AlertTriangle className="size-4" />}>
        <div className="flex-1">
          <p className="font-medium">{t('bandTitleNeedsAction')}</p>
          <p className="text-xs text-muted-foreground">{needsActionReasonCopy(t, state.reason)}</p>
        </div>
        {props.onConnect && (
          <Button size="sm" onClick={props.onConnect}>
            {t('connectButton', { provider: props.providerLabel ?? '' })}
          </Button>
        )}
      </Banner>
    )
  }

  return null
}

// ---------- sub-components ---------- //

function ReadyBand({
  state,
  t,
  disconnectOptions,
}: {
  state: Extract<AuthBandState, { kind: 'ready' }>
  t: BandT
  disconnectOptions: DisconnectOption[]
}) {
  const message =
    state.subkind === 'no_credential'
      ? t('readyNoCredential')
      : t('readyWithCredential', { source: state.source ?? '' })

  return (
    <Banner color="emerald" icon={<CheckCircle2 className="size-4" />}>
      <div className="flex-1">
        <p className="font-medium">{message}</p>
      </div>
      {state.subkind === 'with_credential' && disconnectOptions.length > 0 && (
        <DropdownMenu>
          <DropdownMenuTrigger
            aria-label={t('disconnectMenu')}
            className="inline-flex h-8 items-center justify-center rounded-md px-2 text-sm hover:bg-accent disabled:opacity-50"
          >
            <MoreHorizontal className="size-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {disconnectOptions.map((opt) => (
              <DropdownMenuItem key={opt.scope} onSelect={opt.onClick}>
                {opt.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </Banner>
  )
}

function AwaitingBand({
  state,
  t,
}: {
  state: Extract<AuthBandState, { kind: 'awaiting-others' }>
  t: BandT
}) {
  const who = state.who === 'org_admin' ? t('whoOrgAdmin') : t('whoWorkspaceAdmin')
  const scopeLabel = state.who === 'org_admin' ? 'org' : 'workspace'
  return (
    <Banner color="amber" icon={<Clock className="size-4" />}>
      <div className="flex-1">
        <p className="font-medium">{t('bandTitleAwaiting', { who })}</p>
        <p className="text-xs text-muted-foreground">
          {awaitingReasonCopy(t, state.reason, who, scopeLabel)}
        </p>
      </div>
      <span
        title={t('notifyTooltip')}
        // Notify is a future affordance — render disabled so the layout slot is
        // accounted for. Spec §3.4.
      >
        <Button size="sm" variant="outline" disabled>
          {t('notifyButton')}
        </Button>
      </span>
    </Banner>
  )
}

function StaticTokenForm({
  t,
  reason,
  onSave,
}: {
  t: BandT
  reason: AuthReason
  onSave?: (token: string) => void
}) {
  const [token, setToken] = useState('')

  return (
    <Banner color="amber" icon={<AlertTriangle className="size-4" />}>
      <div className="flex flex-1 flex-col gap-2">
        <div>
          <p className="font-medium">{t('bandTitleNeedsAction')}</p>
          <p className="text-xs text-muted-foreground">{needsActionReasonCopy(t, reason)}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            name="mcp-band-static-token"
            autoComplete="new-password"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            placeholder={t('staticTokenLabel')}
            className="max-w-xs"
            aria-label={t('staticTokenLabel')}
          />
          <Button
            size="sm"
            disabled={!token || !onSave}
            onClick={() => {
              if (!token || !onSave) return
              onSave(token)
              setToken('')
            }}
          >
            {t('staticTokenSave')}
          </Button>
        </div>
      </div>
    </Banner>
  )
}

// ---------- banner shell ---------- //

const BANNER_COLORS = {
  emerald: 'border-success-border bg-success-surface text-success-fg',
  amber: 'border-warning-border bg-warning-surface text-warning-fg',
  rose: 'border-danger-border bg-danger-surface text-danger-fg',
} as const

function Banner({
  color,
  icon,
  children,
}: {
  color: keyof typeof BANNER_COLORS
  icon: ReactNode
  children: ReactNode
}) {
  return (
    <div
      role="status"
      data-testid="mcp-auth-band"
      className={cn(
        'flex items-start gap-3 rounded-lg border px-3 py-2.5 text-sm',
        BANNER_COLORS[color],
      )}
    >
      <span className="mt-0.5 shrink-0">{icon}</span>
      {children}
    </div>
  )
}

// ---------- copy helpers ---------- //

function needsActionReasonCopy(t: BandT, reason: AuthReason): string {
  switch (reason) {
    case 'pending_oauth':
      return t('reasonPendingOAuth')
    case 'missing_org_grant':
      return t('reasonMissingOrgGrantSelf')
    case 'missing_workspace_grant':
      return t('reasonMissingWsGrantSelf')
    case 'user_needs_connection':
      return t('reasonUserNeedsConnection')
    case 'grant_expired':
      return t('reasonGrantExpiredSelf')
    default:
      return ''
  }
}

function awaitingReasonCopy(t: BandT, reason: AuthReason, who: string, scope: string): string {
  switch (reason) {
    case 'pending_oauth':
      return t('reasonAwaitingPendingOauth', { who })
    case 'grant_expired':
      return t('reasonAwaitingExpired', { scope })
    case 'missing_org_grant':
    case 'missing_workspace_grant':
    case 'user_needs_connection':
    default:
      return t('reasonAwaitingMissingOrg', { who })
  }
}

function errorReasonCopy(t: BandT, reason: string | undefined): string {
  if (!reason) return ''
  if (reason === 'popup_blocked') return t('errorPopupBlocked')
  if (reason === 'timeout') return t('errorTimeout')
  if (reason.startsWith('start_failed:')) {
    return reason.slice('start_failed:'.length)
  }
  return reason
}
