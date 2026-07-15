'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Info } from 'lucide-react'

import type { ImAccount, ImBotSettings, ImIdentityLink } from '@cubeplex/core'
import {
  createApiClient,
  wsGetImBotSettings,
  wsListIdentityLinks,
  wsUpdateImBotSettings,
} from '@cubeplex/core'
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
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
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

  const botName = account.bot_app_name || 'CubePlex'
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
      setSettingsError(err instanceof Error ? err.message : t('botSettings.saveError'))
    } finally {
      if (currentAccountId.current === savingAccountId) setSaving(false)
    }
  }, [client, account.workspace_id, account.id, settings, t])

  const behaviorSummary = useMemo(() => {
    if (settings === null) return null
    const shared = settings.routing_mode === 'shared'
    const flat = settings.topic_mode === 'flat'

    const group: string[] = [
      shared
        ? t('botSettings.summary.group.routing.shared')
        : t('botSettings.summary.group.routing.isolated'),
    ]
    if (flat) {
      group.push(t('botSettings.summary.group.topic.flat'))
    } else if (shared) {
      group.push(t('botSettings.summary.group.topic.sharedTopic'))
    } else {
      group.push(t('botSettings.summary.group.topic.isolatedTopic'))
    }
    if (shared) {
      group.push(
        settings.sandbox_mode === 'creator'
          ? t('botSettings.summary.group.sandbox.creator')
          : t('botSettings.summary.group.sandbox.dedicated'),
      )
    } else {
      // Isolated has no sandbox selector, but the implicit behavior still
      // deserves a sentence — each user runs in their own mapped sandbox.
      group.push(t('botSettings.summary.group.sandbox.isolated'))
    }

    const dm: string[] = [
      t('botSettings.summary.dm.base'),
      flat
        ? t('botSettings.summary.dm.topic.flat')
        : t('botSettings.summary.dm.topic.topic', { botName }),
    ]

    return { group, dm }
  }, [settings, botName, t])

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
      <div className="flex w-full max-w-2xl flex-col gap-4 text-sm">
        <section>
          <h3 className="mb-2 text-xs uppercase text-muted-foreground">
            {t('botSettings.section.identity')}
          </h3>
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-xs">
            <dt className="text-muted-foreground">{t('botSettings.field.actingAs')}</dt>
            <dd>{account.acting_user_id}</dd>
            <dt className="text-muted-foreground">{t('botSettings.field.botOpenId')}</dt>
            <dd className="truncate">{account.runtime.bot_open_id ?? '—'}</dd>
            <dt className="text-muted-foreground">{t('botSettings.field.mode')}</dt>
            <dd>{account.delivery_mode}</dd>
          </dl>
        </section>

        {settingsScoped && (
          <>
            <Separator />

            <section>
              <h3 className="mb-2 text-xs uppercase text-muted-foreground">
                {t('botSettings.section.behavior')}
              </h3>
              {settings === null ? (
                <p className="text-xs text-muted-foreground">{t('botSettings.loading')}</p>
              ) : (
                <TooltipProvider>
                  <div className="flex flex-col gap-3">
                    <div className="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-3">
                      <FieldLabel
                        htmlFor="im-routing-mode"
                        label={t('botSettings.field.routing')}
                        help={
                          <FieldHelpContent
                            title={t('botSettings.field.routing')}
                            body={t('botSettings.help.routing.body')}
                            options={[
                              {
                                name: t('botSettings.routing.isolated'),
                                desc: t('botSettings.help.routing.isolated'),
                              },
                              {
                                name: t('botSettings.routing.shared'),
                                desc: t('botSettings.help.routing.shared'),
                              },
                            ]}
                          />
                        }
                        helpAria={t('botSettings.help.infoAria')}
                      />
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
                              routing_mode === 'shared'
                                ? settings.sandbox_mode || 'dedicated'
                                : null,
                          })
                        }}
                      >
                        <SelectTrigger id="im-routing-mode" size="sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="isolated">
                            {t('botSettings.routing.isolated')}
                          </SelectItem>
                          <SelectItem value="shared" disabled={!sharedSupported}>
                            {t('botSettings.routing.shared')}
                            {!sharedSupported && t('botSettings.routing.sharedNotSupportedSuffix')}
                          </SelectItem>
                        </SelectContent>
                      </Select>
                      {!sharedSupported && (
                        <>
                          <span />
                          <p className="text-xs text-muted-foreground">
                            {t('botSettings.routing.platformOnlyPerPerson')}
                          </p>
                        </>
                      )}

                      {settings.routing_mode === 'shared' && (
                        <>
                          <FieldLabel
                            htmlFor="im-sandbox-mode"
                            label={t('botSettings.field.sandbox')}
                            help={
                              <FieldHelpContent
                                title={t('botSettings.field.sandbox')}
                                body={t('botSettings.help.sandbox.body')}
                                options={[
                                  {
                                    name: t('botSettings.sandboxMode.dedicated'),
                                    desc: t('botSettings.help.sandbox.dedicated'),
                                  },
                                  {
                                    name: t('botSettings.sandboxMode.creator'),
                                    desc: t('botSettings.help.sandbox.creator'),
                                  },
                                ]}
                              />
                            }
                            helpAria={t('botSettings.help.infoAria')}
                          />
                          <Select
                            value={settings.sandbox_mode ?? 'dedicated'}
                            onValueChange={(v) => setSettings({ ...settings, sandbox_mode: v })}
                          >
                            <SelectTrigger id="im-sandbox-mode" size="sm">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="dedicated">
                                {t('botSettings.sandboxMode.dedicated')}
                              </SelectItem>
                              <SelectItem value="creator">
                                {t('botSettings.sandboxMode.creator')}
                              </SelectItem>
                            </SelectContent>
                          </Select>
                        </>
                      )}

                      <FieldLabel
                        htmlFor="im-topic-mode"
                        label={t('botSettings.field.topicMode')}
                        help={
                          <FieldHelpContent
                            title={t('botSettings.field.topicMode')}
                            body={t('botSettings.help.topic.body')}
                            options={[
                              {
                                name: t('botSettings.topic.topic'),
                                desc: t('botSettings.help.topic.topic'),
                              },
                              {
                                name: t('botSettings.topic.flat'),
                                desc: t('botSettings.help.topic.flat'),
                              },
                            ]}
                          />
                        }
                        helpAria={t('botSettings.help.infoAria')}
                      />
                      <Select
                        value={settings.topic_mode}
                        onValueChange={(v) =>
                          setSettings({
                            ...settings,
                            topic_mode: v as ImBotSettings['topic_mode'],
                          })
                        }
                      >
                        <SelectTrigger id="im-topic-mode" size="sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="topic">{t('botSettings.topic.topic')}</SelectItem>
                          <SelectItem value="flat">{t('botSettings.topic.flat')}</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>

                    {behaviorSummary && (
                      <div className="space-y-2.5 rounded-md border border-border/60 bg-muted/30 p-3 text-xs">
                        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                          {t('botSettings.summary.heading')}
                        </p>
                        <SummarySection
                          label={t('botSettings.summary.group.label')}
                          items={behaviorSummary.group}
                        />
                        <SummarySection
                          label={t('botSettings.summary.dm.label')}
                          items={behaviorSummary.dm}
                        />
                      </div>
                    )}
                    {settingsError && <p className="text-xs text-destructive">{settingsError}</p>}

                    <div>
                      <Button size="sm" disabled={!settingsDirty || saving} onClick={saveSettings}>
                        {saving ? t('botSettings.action.saving') : t('botSettings.action.save')}
                      </Button>
                    </div>
                  </div>
                </TooltipProvider>
              )}
            </section>
          </>
        )}

        <Separator />

        <section>
          <h3 className="mb-2 text-xs uppercase text-muted-foreground">
            {t('botSettings.section.identityGate')}
          </h3>
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

interface FieldLabelProps {
  htmlFor: string
  label: string
  help: React.ReactNode
  helpAria: string
}

function FieldLabel({ htmlFor, label, help, helpAria }: FieldLabelProps): React.ReactElement {
  return (
    <div className="flex items-center gap-1">
      <Label htmlFor={htmlFor}>{label}</Label>
      <Tooltip>
        <TooltipTrigger
          type="button"
          aria-label={helpAria}
          className="inline-flex size-4 items-center justify-center text-muted-foreground transition-colors hover:text-foreground"
        >
          <Info className="size-3.5" />
        </TooltipTrigger>
        <TooltipContent className="max-w-sm whitespace-normal px-3 py-2 text-xs leading-relaxed">
          {help}
        </TooltipContent>
      </Tooltip>
    </div>
  )
}

interface SummarySectionProps {
  label: string
  items: string[]
}

function SummarySection({ label, items }: SummarySectionProps): React.ReactElement {
  return (
    <div>
      <p className="font-medium text-foreground">{label}</p>
      <ul className="mt-1 ml-4 list-disc space-y-1 text-muted-foreground">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  )
}

interface FieldHelpContentProps {
  title: string
  body: string
  options: { name: string; desc: string }[]
}

function FieldHelpContent({ title, body, options }: FieldHelpContentProps): React.ReactElement {
  return (
    <div className="space-y-2 text-left">
      <p className="font-medium">{title}</p>
      <p className="text-background/85">{body}</p>
      <ul className="space-y-1.5">
        {options.map((o) => (
          <li key={o.name}>
            <span className="font-medium">{o.name}</span>
            <span className="text-background/85"> — {o.desc}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
