'use client'

/**
 * Authentication action band — five mutually exclusive states.
 * Spec: docs/superpowers/specs/2026-05-16-mcp-install-auth-handoff-spec.md §3.
 */

import { useState, type ReactNode } from 'react'
import { useTranslations } from 'next-intl'
import { AlertTriangle, CheckCircle2, Clock, MoreHorizontal, XCircle } from 'lucide-react'
import {
  runOAuthFlow,
  wsCreateMyGrant,
  wsCreateWorkspaceGrant,
  adminCreateOrgGrant,
  wsMyGrantOAuthStart,
  wsWorkspaceGrantOAuthStart,
  adminOrgGrantOAuthStart,
  wsDeleteMyGrant,
  wsDeleteWorkspaceGrant,
  adminDeleteOrgGrant,
  type ApiClient,
  type MCPEffectiveConnector,
  type MCPOAuthStartResult,
} from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'

import { computeAuthBandState, type AuthBandState, type AuthReason } from './effectiveAuthState'

type Scope = 'org' | 'workspace' | 'user'
type BandT = ReturnType<typeof useTranslations<'mcp.auth'>>

export interface AuthActionBandProps {
  connector: MCPEffectiveConnector
  client: ApiClient
  /** For workspace-scope OAuth/grant calls, lens workspace id. */
  wsId: string
  callerRole: 'admin' | 'member'
  isOrgAdmin: boolean
  onChanged: () => Promise<void>
}

export function AuthActionBand(props: AuthActionBandProps) {
  const t = useTranslations('mcp.auth')
  const state = computeAuthBandState({
    connector: props.connector,
    callerRole: props.callerRole,
    isOrgAdmin: props.isOrgAdmin,
  })
  const [inFlight, setInFlight] = useState(false)
  const [errorState, setErrorState] = useState<{ reason?: string } | null>(null)

  if (state.kind === 'hidden') return null

  // Scope the connect / save / delete action targets. Mirrors §4.
  const scope = scopeForBand(props.connector)

  if (state.kind === 'ready') {
    return <ReadyBand state={state} t={t} scope={scope} props={props} />
  }

  if (state.kind === 'awaiting-others') {
    return <AwaitingBand state={state} t={t} />
  }

  if (state.kind === 'oauth-in-flight' || inFlight) {
    return (
      <Banner color="amber" icon={<Clock className="size-4" />}>
        <div className="flex-1">
          <p className="font-medium">{t('bandTitleInFlight')}</p>
        </div>
      </Banner>
    )
  }

  if (errorState || state.kind === 'error') {
    const reason = errorState?.reason ?? (state.kind === 'error' ? state.reason : undefined)
    return (
      <Banner color="rose" icon={<XCircle className="size-4" />}>
        <div className="flex-1">
          <p className="font-medium">{t('bandTitleError')}</p>
          <p className="text-xs text-muted-foreground">{errorReasonCopy(t, reason)}</p>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            setErrorState(null)
          }}
        >
          {t('retryButton')}
        </Button>
      </Banner>
    )
  }

  if (state.kind === 'needs-action') {
    if (props.connector.install.auth_method === 'oauth') {
      const onConnect = async (): Promise<void> => {
        setInFlight(true)
        setErrorState(null)
        const startPost = oauthStartFn(scope, props)
        const result = await runOAuthFlow({ startPost })
        setInFlight(false)
        if (result.status === 'ok') {
          await props.onChanged()
          return
        }
        if (result.status === 'cancelled') return
        setErrorState({ reason: result.reason })
      }
      return (
        <Banner color="amber" icon={<AlertTriangle className="size-4" />}>
          <div className="flex-1">
            <p className="font-medium">{t('bandTitleNeedsAction')}</p>
            <p className="text-xs text-muted-foreground">
              {needsActionReasonCopy(t, state.reason)}
            </p>
          </div>
          <Button size="sm" onClick={() => void onConnect()}>
            {t('connectButton', { provider: providerLabel(props.connector) })}
          </Button>
        </Banner>
      )
    }

    // static
    return (
      <StaticTokenForm
        scope={scope}
        props={props}
        t={t}
        reason={state.reason}
        onError={(reason) => setErrorState({ reason })}
      />
    )
  }

  return null
}

// ---------- sub-components ---------- //

function ReadyBand({
  state,
  t,
  scope,
  props,
}: {
  state: Extract<AuthBandState, { kind: 'ready' }>
  t: BandT
  scope: Scope
  props: AuthActionBandProps
}) {
  const [busy, setBusy] = useState(false)
  const message =
    state.subkind === 'no_credential'
      ? t('readyNoCredential')
      : t('readyWithCredential', { source: state.source ?? '' })

  const disconnects = disconnectsForCaller(props, state.source)

  const onDelete = async (target: Scope): Promise<void> => {
    setBusy(true)
    try {
      if (target === 'org') {
        await adminDeleteOrgGrant(props.client, props.connector.install.install_id)
      } else if (target === 'workspace') {
        await wsDeleteWorkspaceGrant(props.client, props.wsId, props.connector.install.install_id)
      } else {
        await wsDeleteMyGrant(props.client, props.wsId, props.connector.install.install_id)
      }
      await props.onChanged()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Banner color="emerald" icon={<CheckCircle2 className="size-4" />}>
      <div className="flex-1">
        <p className="font-medium">{message}</p>
      </div>
      {state.subkind === 'with_credential' && disconnects.length > 0 && (
        <DropdownMenu>
          <DropdownMenuTrigger
            disabled={busy}
            aria-label={t('disconnectMenu')}
            className="inline-flex h-8 items-center justify-center rounded-md px-2 text-sm hover:bg-accent disabled:opacity-50"
          >
            <MoreHorizontal className="size-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {disconnects.includes('org') && (
              <DropdownMenuItem onSelect={() => void onDelete('org')}>
                {t('removeOrgGrant')}
              </DropdownMenuItem>
            )}
            {disconnects.includes('workspace') && (
              <DropdownMenuItem onSelect={() => void onDelete('workspace')}>
                {t('removeWsGrant')}
              </DropdownMenuItem>
            )}
            {disconnects.includes('user') && (
              <DropdownMenuItem onSelect={() => void onDelete('user')}>
                {t('removeMyGrant')}
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
      {/* `scope` is referenced so the lint rule does not flag the parameter. */}
      <span data-scope={scope} className="hidden" aria-hidden="true" />
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
  scope,
  props,
  t,
  reason,
  onError,
}: {
  scope: Scope
  props: AuthActionBandProps
  t: BandT
  reason: AuthReason
  onError: (reason: string) => void
}) {
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)

  const onSave = async (): Promise<void> => {
    if (!token) return
    setBusy(true)
    try {
      const body = { credential_plaintext: token }
      const installId = props.connector.install.install_id
      if (scope === 'org') {
        await adminCreateOrgGrant(props.client, installId, body)
      } else if (scope === 'workspace') {
        await wsCreateWorkspaceGrant(props.client, props.wsId, installId, body)
      } else {
        await wsCreateMyGrant(props.client, props.wsId, installId, body)
      }
      setToken('')
      await props.onChanged()
    } catch (err) {
      onError(`save_failed:${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

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
            placeholder={t('staticTokenLabel')}
            className="max-w-xs"
            aria-label={t('staticTokenLabel')}
          />
          <Button size="sm" disabled={busy || !token} onClick={() => void onSave()}>
            {t('staticTokenSave')}
          </Button>
        </div>
      </div>
    </Banner>
  )
}

// ---------- banner shell ---------- //

const BANNER_COLORS = {
  emerald:
    'border-emerald-300/60 bg-emerald-50 text-emerald-900 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-100',
  amber:
    'border-amber-300/60 bg-amber-50 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100',
  rose: 'border-rose-300/60 bg-rose-50 text-rose-900 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-100',
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

// ---------- helpers ---------- //

function scopeForBand(connector: MCPEffectiveConnector): Scope {
  const r = connector.required_grant_scope
  if (r === 'org' || r === 'workspace' || r === 'user') return r
  // Fallback: derive from credential_policy. Spec §4 admin-row override is
  // handled by the caller pre-synthesizing `required_grant_scope='org'`.
  const policy = connector.credential_policy
  if (policy === 'org' || policy === 'workspace' || policy === 'user') return policy
  return 'user'
}

function oauthStartFn(
  scope: Scope,
  props: AuthActionBandProps,
): () => Promise<MCPOAuthStartResult> {
  const installId = props.connector.install.install_id
  if (scope === 'org') return () => adminOrgGrantOAuthStart(props.client, installId)
  if (scope === 'workspace')
    return () => wsWorkspaceGrantOAuthStart(props.client, props.wsId, installId)
  return () => wsMyGrantOAuthStart(props.client, props.wsId, installId)
}

function providerLabel(connector: MCPEffectiveConnector): string {
  return connector.template?.provider || connector.template?.name || connector.install.name
}

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
  return reason
}

/**
 * Which "remove X grant" entries to show in the disconnect menu, based on the
 * caller's authority. Spec §4.
 */
function disconnectsForCaller(
  props: AuthActionBandProps,
  source: 'org' | 'workspace' | 'user' | undefined,
): Scope[] {
  const items: Scope[] = []
  // Only offer to remove the layer that actually supplied the credential —
  // we don't want a member to be able to revoke an org grant even if they
  // see "credential from org" in their detail panel.
  if (source === 'org' && props.isOrgAdmin) items.push('org')
  if (source === 'workspace' && (props.isOrgAdmin || props.callerRole === 'admin'))
    items.push('workspace')
  if (source === 'user') items.push('user')
  return items
}
