'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'

import type { ImAccount, ImBotSettings, ImIdentityLink } from '@cubebox/core'
import {
  createApiClient,
  wsGetImBotSettings,
  wsListIdentityLinks,
  wsUpdateImBotSettings,
} from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { DetailPanel } from '@/components/shared/DetailPanel'

import { ImAccountStatusPill } from './ImAccountStatusPill'
import { PlatformLogo } from './PlatformLogo'

interface Props {
  account: ImAccount
  scope: 'workspace' | 'admin'
  onDisable: () => void
  onEnable: () => void
  onDelete: () => void
  onBack?: () => void
  backLabel?: string
}

export function ImAccountDetailPanel({
  account,
  scope,
  onDisable,
  onEnable,
  onDelete,
  onBack,
  backLabel,
}: Props): React.ReactElement {
  const t = useTranslations('im')
  const client = useMemo(() => createApiClient(''), [])
  const [links, setLinks] = useState<ImIdentityLink[]>([])
  const [settings, setSettings] = useState<ImBotSettings | null>(null)
  const [savedSettings, setSavedSettings] = useState<ImBotSettings | null>(null)
  const [saving, setSaving] = useState(false)
  const [settingsError, setSettingsError] = useState<string | null>(null)

  const loadLinks = useCallback(async () => {
    try {
      const res = await wsListIdentityLinks(client, account.workspace_id, account.id)
      setLinks(res.links)
    } catch {
      // silently ignore — read-only, non-critical
    }
  }, [client, account.workspace_id, account.id])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- load-on-mount
    void loadLinks()
  }, [loadLinks])

  // Bot settings are workspace-scoped (the GET route is guarded by
  // require_member). The admin page can surface accounts from other
  // workspaces, where this caller would 403 — so only load + render the
  // section in workspace scope. On account switch, clear state and ignore a
  // late response so account B never shows / saves account A's form.
  const settingsScoped = scope === 'workspace'
  // The latest account this panel instance is showing. Both the load effect
  // and the save handler check it so a response for account A never lands on
  // account B after the panel is reused.
  const currentAccountId = useRef(account.id)
  useEffect(() => {
    currentAccountId.current = account.id
  }, [account.id])
  useEffect(() => {
    if (!settingsScoped) return
    let active = true
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset on account switch
    setSettings(null)
    setSavedSettings(null)
    setSettingsError(null)
    // Clear any in-flight save state too: a save started for the previous
    // account skips its own setSaving(false) once the id changes, which would
    // otherwise leave the new account's Save button stuck disabled.
    setSaving(false)
    void (async () => {
      try {
        const res = await wsGetImBotSettings(client, account.workspace_id, account.id)
        if (active) {
          setSettings(res)
          setSavedSettings(res)
        }
      } catch {
        // non-critical; the section stays in its loading state
      }
    })()
    return () => {
      active = false
    }
  }, [client, account.workspace_id, account.id, settingsScoped])

  const botName = account.bot_app_name || 'cubebox'
  // Shared routing needs a channel-wide scope. The Teams connector only emits
  // per-sender scopes, so shared would silently produce one group conversation
  // per sender — disable it there.
  const sharedSupported = account.platform !== 'teams'
  const settingsDirty =
    settings !== null &&
    savedSettings !== null &&
    (settings.routing_mode !== savedSettings.routing_mode ||
      settings.topic_mode !== savedSettings.topic_mode ||
      (settings.sandbox_mode ?? '') !== (savedSettings.sandbox_mode ?? ''))

  const saveSettings = useCallback(async () => {
    if (settings === null) return
    const savingAccountId = account.id
    setSaving(true)
    setSettingsError(null)
    try {
      const payload: ImBotSettings = {
        ...settings,
        // Shared requires a sandbox_mode (default it); outside shared the
        // field is hidden, so clear it — otherwise a stale value would still
        // apply to isolated topics.
        sandbox_mode:
          settings.routing_mode === 'shared' ? settings.sandbox_mode || 'dedicated' : null,
      }
      const res = await wsUpdateImBotSettings(client, account.workspace_id, account.id, payload)
      // Drop the response if the panel has since switched accounts.
      if (currentAccountId.current !== savingAccountId) return
      setSettings(res)
      setSavedSettings(res)
    } catch (err) {
      if (currentAccountId.current !== savingAccountId) return
      setSettingsError(err instanceof Error ? err.message : 'Failed to save settings')
    } finally {
      if (currentAccountId.current === savingAccountId) setSaving(false)
    }
  }, [client, account.workspace_id, account.id, settings])

  const behaviorSummary = useMemo(() => {
    if (settings === null) return ''
    const shared = settings.routing_mode === 'shared'
    const routing = shared
      ? 'Everyone in a channel shares one conversation.'
      : 'Each person gets their own conversation.'
    let topic: string
    if (!shared && settings.topic_mode === 'flat') {
      topic = 'Standalone conversations, no topic grouping.'
    } else if (shared) {
      // Shared groups the whole channel under one topic; DMs stay per-sender.
      topic = `Grouped under a topic — one per channel; DMs are titled “${botName}”.`
    } else {
      // Isolated keeps the sender scope, so topics are per person, not per channel.
      topic = `Grouped under a topic — one per person (DMs titled “${botName}”).`
    }
    return `${routing} ${topic}`
  }, [settings, botName])

  return (
    <DetailPanel
      onBack={onBack}
      backLabel={backLabel}
      title={account.bot_app_name ?? account.external_account_id}
      badge={
        <ImAccountStatusPill
          connectionState={account.runtime.connection_state}
          enabled={account.enabled}
        />
      }
      subtitle={
        <span className="inline-flex items-center gap-1.5">
          <PlatformLogo platform={account.platform} className="size-3.5" />
          {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
          <span>{t(`platform.${account.platform}.label` as any)}</span>
          {account.bot_app_name && (
            <>
              <span className="mx-0.5 text-muted-foreground/50">·</span>
              <span>{account.external_account_id}</span>
            </>
          )}
        </span>
      }
      actions={
        <>
          {account.enabled ? (
            <Button variant="outline" size="sm" onClick={onDisable}>
              {t('action.disable')}
            </Button>
          ) : (
            <Button variant="outline" size="sm" onClick={onEnable}>
              {t('action.enable')}
            </Button>
          )}
          {scope === 'workspace' && (
            <Button variant="destructive" size="sm" onClick={onDelete}>
              {t('action.delete')}
            </Button>
          )}
        </>
      }
    >
      <div className="flex max-w-2xl flex-col gap-4 text-sm">
        <section>
          <h3 className="mb-2 text-xs uppercase text-muted-foreground">Identity</h3>
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-xs">
            <dt className="text-muted-foreground">Acting as</dt>
            <dd>{account.acting_user_id}</dd>
            <dt className="text-muted-foreground">Bot open_id</dt>
            <dd className="truncate">{account.runtime.bot_open_id ?? '—'}</dd>
            <dt className="text-muted-foreground">Mode</dt>
            <dd>{account.delivery_mode}</dd>
          </dl>
        </section>

        {settingsScoped && (
          <>
            <Separator />

            <section>
              <h3 className="mb-2 text-xs uppercase text-muted-foreground">Behavior</h3>
              {settings === null ? (
                <p className="text-xs text-muted-foreground">Loading…</p>
              ) : (
                <div className="flex flex-col gap-3">
                  <div className="grid grid-cols-[7rem_1fr] items-center gap-x-4 gap-y-3">
                    <Label htmlFor="im-routing-mode">Routing</Label>
                    <Select
                      value={settings.routing_mode}
                      onValueChange={(v) => {
                        const routing_mode = v as ImBotSettings['routing_mode']
                        setSettings({
                          ...settings,
                          routing_mode,
                          // Give shared a valid sandbox up front; clear it
                          // when leaving shared (the field is hidden).
                          sandbox_mode:
                            routing_mode === 'shared' ? settings.sandbox_mode || 'dedicated' : null,
                        })
                      }}
                    >
                      <SelectTrigger id="im-routing-mode" size="sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="isolated">Isolated (per person)</SelectItem>
                        <SelectItem value="shared" disabled={!sharedSupported}>
                          Shared (per channel)
                          {!sharedSupported && ' — not supported'}
                        </SelectItem>
                      </SelectContent>
                    </Select>
                    {!sharedSupported && (
                      <>
                        <span />
                        <p className="text-xs text-muted-foreground">
                          This platform only supports per-person routing.
                        </p>
                      </>
                    )}

                    <Label htmlFor="im-topic-mode">Topic grouping</Label>
                    <Select
                      value={settings.topic_mode}
                      onValueChange={(v) =>
                        setSettings({ ...settings, topic_mode: v as ImBotSettings['topic_mode'] })
                      }
                    >
                      <SelectTrigger id="im-topic-mode" size="sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="topic">Group under a topic</SelectItem>
                        <SelectItem value="flat">Standalone conversations</SelectItem>
                      </SelectContent>
                    </Select>

                    {settings.routing_mode === 'shared' && (
                      <>
                        <Label htmlFor="im-sandbox-mode">Sandbox</Label>
                        <Select
                          value={settings.sandbox_mode ?? 'dedicated'}
                          onValueChange={(v) => setSettings({ ...settings, sandbox_mode: v })}
                        >
                          <SelectTrigger id="im-sandbox-mode" size="sm">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="dedicated">Dedicated (topic-owned)</SelectItem>
                            <SelectItem value="creator">Creator&apos;s sandbox</SelectItem>
                          </SelectContent>
                        </Select>
                      </>
                    )}
                  </div>

                  <p className="text-xs text-muted-foreground">{behaviorSummary}</p>
                  {settingsError && <p className="text-xs text-destructive">{settingsError}</p>}

                  <div>
                    <Button size="sm" disabled={!settingsDirty || saving} onClick={saveSettings}>
                      {saving ? 'Saving…' : 'Save'}
                    </Button>
                  </div>
                </div>
              )}
            </section>
          </>
        )}

        <Separator />

        <section>
          <h3 className="mb-2 text-xs uppercase text-muted-foreground">Identity gate (24h)</h3>
          <p className="text-xs">
            {t('runtime.gate.matched', { count: account.runtime.matched_24h })}
            {' · '}
            {t('runtime.gate.rejected', { count: account.runtime.rejected_24h })}
          </p>
        </section>

        <Separator />

        <section>
          <h3 className="mb-2 text-xs uppercase text-muted-foreground">
            {t('identityLinks.title')}
          </h3>
          {links.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t('identityLinks.empty')}</p>
          ) : (
            <div className="space-y-2">
              {links.map((link) => (
                <div
                  key={link.id}
                  className="flex items-center justify-between rounded-md border px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="truncate text-xs font-medium">
                      {link.user_display_name || link.user_email}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">{link.user_email}</p>
                  </div>
                  <span className="shrink-0 text-xs text-muted-foreground">{link.im_user_id}</span>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </DetailPanel>
  )
}
