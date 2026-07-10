'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { createApiClient, wsListDingtalkApps, type DingtalkAppInfo } from '@cubebox/core'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

import type { WizardStepProps } from '../platforms/types'

type DynamicT = (key: string, values?: Record<string, string | number>) => string

export function StepDingtalkCredentials({
  form,
  onChange,
  wsId,
}: WizardStepProps): React.ReactElement {
  const t = useTranslations() as unknown as DynamicT
  const client = useMemo(() => createApiClient(''), [])
  const [apps, setApps] = useState<DingtalkAppInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [fetchedFor, setFetchedFor] = useState('')
  const abortRef = useRef<AbortController | null>(null)

  const appKey = form.app_key ?? ''
  const appSecret = form.app_secret ?? ''
  const credsKey = `${appKey}:${appSecret}`

  const fetchApps = useCallback(async () => {
    if (!appKey || !appSecret || !wsId) return
    if (fetchedFor === credsKey) return
    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac

    setLoading(true)
    try {
      const result = await wsListDingtalkApps(client, wsId, appKey, appSecret)
      if (ac.signal.aborted) return
      setApps(result.apps)
      setFetchedFor(credsKey)
      if (result.apps.length === 1) {
        const app = result.apps[0]
        onChange({
          _dt_agent_id: String(app.agent_id),
          bot_name: app.name,
          bot_avatar_url: app.icon_url,
        })
      }
    } catch {
      if (ac.signal.aborted) return
      setApps([])
      setFetchedFor(credsKey)
    } finally {
      if (!ac.signal.aborted) setLoading(false)
    }
  }, [appKey, appSecret, credsKey, fetchedFor, client, wsId, onChange])

  useEffect(() => {
    if (appKey.length >= 10 && appSecret.length >= 10 && fetchedFor !== credsKey) {
      const timer = setTimeout(fetchApps, 600)
      return () => clearTimeout(timer)
    }
  }, [appKey, appSecret, credsKey, fetchedFor, fetchApps])

  const handleAppSelect = useCallback(
    (agentId: string | null) => {
      if (!agentId) return
      const app = apps.find((a) => String(a.agent_id) === agentId)
      if (app) {
        onChange({
          _dt_agent_id: agentId,
          bot_name: app.name,
          bot_avatar_url: app.icon_url,
        })
      }
    },
    [apps, onChange],
  )

  return (
    <div className="grid grid-cols-2 gap-3">
      <div className="space-y-1">
        <Label htmlFor="cred-app_key">{t('im.wizard.dingtalk.field.appKey')}</Label>
        <Input
          id="cred-app_key"
          type="text"
          required
          placeholder="ding..."
          value={appKey}
          onChange={(e) => onChange({ app_key: e.target.value })}
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor="cred-app_secret">{t('im.wizard.dingtalk.field.appSecret')}</Label>
        <Input
          id="cred-app_secret"
          type="password"
          name="dingtalk-app-secret"
          required
          value={appSecret}
          onChange={(e) => onChange({ app_secret: e.target.value })}
          autoComplete="new-password"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
        />
      </div>

      {loading && (
        <div className="col-span-2 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" />
          {t('im.wizard.dingtalk.loadingApps')}
        </div>
      )}

      {!loading && apps.length > 1 && (
        <div className="col-span-2 space-y-1">
          <Label htmlFor="cred-dt-app">{t('im.wizard.dingtalk.field.selectApp')}</Label>
          <Select value={form._dt_agent_id ?? ''} onValueChange={handleAppSelect}>
            <SelectTrigger id="cred-dt-app">
              <SelectValue placeholder={t('im.wizard.dingtalk.selectAppPlaceholder')} />
            </SelectTrigger>
            <SelectContent>
              {apps.map((app) => (
                <SelectItem key={app.agent_id} value={String(app.agent_id)}>
                  {app.name}
                  {app.desc && app.desc !== app.name && (
                    <span className="ml-1 text-xs text-muted-foreground">— {app.desc}</span>
                  )}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {!loading && apps.length === 1 && form.bot_name && (
        <div className="col-span-2 text-sm text-muted-foreground">
          {t('im.wizard.dingtalk.autoDetected', { name: form.bot_name })}
        </div>
      )}

      {!loading && fetchedFor === credsKey && apps.length === 0 && appKey && appSecret && (
        <div className="col-span-2 space-y-1">
          <Label htmlFor="cred-bot_name">{t('im.wizard.dingtalk.field.botName')}</Label>
          <Input
            id="cred-bot_name"
            type="text"
            value={form.bot_name ?? ''}
            onChange={(e) => onChange({ bot_name: e.target.value })}
          />
        </div>
      )}
    </div>
  )
}
