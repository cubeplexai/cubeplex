'use client'

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { ArrowUpCircle, Download, Trash2, X, Check } from 'lucide-react'
import type { SkillDetail } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { jsonHeaders, csrfHeaders, readApiError } from '@/lib/csrf'

interface OrgInstallActionsProps {
  skill: SkillDetail
  onActionDone: () => void
}

type Action = 'install' | 'upgrade' | 'uninstall' | null

export function OrgInstallActions({ skill, onActionDone }: OrgInstallActionsProps) {
  const t = useTranslations('adminSkills')
  const [busy, setBusy] = useState<Action>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirmUninstall, setConfirmUninstall] = useState(false)

  // Reset confirm dialog whenever install_state changes (e.g. after upgrade or version switch)
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setConfirmUninstall(false)
  }, [skill.install_state])

  async function install(version: string): Promise<void> {
    const res = await fetch(`/api/v1/admin/skills/${skill.id}/install`, {
      method: 'POST',
      credentials: 'include',
      headers: jsonHeaders(),
      body: JSON.stringify({ version }),
    })
    if (!res.ok) throw new Error(await readApiError(res))
  }

  async function uninstall(): Promise<void> {
    const res = await fetch(`/api/v1/admin/skills/${skill.id}/install`, {
      method: 'DELETE',
      credentials: 'include',
      headers: csrfHeaders(),
    })
    if (!res.ok && res.status !== 204) throw new Error(await readApiError(res))
  }

  async function run(action: Exclude<Action, null>, fn: () => Promise<void>): Promise<void> {
    setBusy(action)
    setError(null)
    try {
      await fn()
      onActionDone()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(null)
    }
  }

  const installed =
    skill.install_state === 'installed' || skill.install_state === 'update_available'

  return (
    <div className="flex flex-col items-end gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        {skill.install_state === 'uninstalled' && (
          <Button
            size="sm"
            disabled={busy !== null}
            onClick={() => void run('install', () => install(skill.current_version))}
            data-testid="skill-install-button"
          >
            <Download className="size-3.5" />
            {busy === 'install'
              ? t('installing')
              : t('installAction', { version: skill.current_version })}
          </Button>
        )}

        {skill.install_state === 'update_available' && (
          <Button
            size="sm"
            disabled={busy !== null}
            onClick={() => void run('upgrade', () => install(skill.current_version))}
            data-testid="skill-upgrade-button"
          >
            <ArrowUpCircle className="size-3.5" />
            {busy === 'upgrade'
              ? t('upgrading')
              : t('upgradeAction', { version: skill.current_version })}
          </Button>
        )}

        {installed && !confirmUninstall && (
          <Button
            size="sm"
            variant="ghost"
            className="cursor-pointer text-destructive hover:bg-destructive/10 hover:text-destructive"
            disabled={busy !== null}
            onClick={() => setConfirmUninstall(true)}
            data-testid="skill-uninstall-button"
          >
            <Trash2 className="size-3.5" />
            {t('uninstall')}
          </Button>
        )}

        {installed && confirmUninstall && (
          <div className="flex items-center gap-1.5 rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1.5">
            <span className="text-xs text-destructive">{t('confirmUninstall')}</span>
            <button
              type="button"
              className="cursor-pointer rounded p-0.5 text-destructive hover:bg-destructive/20"
              disabled={busy !== null}
              onClick={() => void run('uninstall', uninstall)}
            >
              <Check className="size-3.5" />
            </button>
            <button
              type="button"
              className="cursor-pointer rounded p-0.5 text-muted-foreground hover:bg-muted"
              onClick={() => setConfirmUninstall(false)}
            >
              <X className="size-3.5" />
            </button>
          </div>
        )}
      </div>

      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  )
}

interface AutoBindToggleProps {
  skill: SkillDetail
  onActionDone: () => void
}

export function AutoBindToggle({ skill, onActionDone }: AutoBindToggleProps) {
  const t = useTranslations('adminSkills')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const installed =
    skill.install_state === 'installed' || skill.install_state === 'update_available'

  if (!installed || skill.auto_bind === null) return null

  async function toggle(nextValue: boolean): Promise<void> {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch(`/api/v1/admin/skills/${skill.id}/install`, {
        method: 'PATCH',
        credentials: 'include',
        headers: jsonHeaders(),
        body: JSON.stringify({ auto_bind: nextValue }),
      })
      if (!res.ok) throw new Error(await readApiError(res))
      onActionDone()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2 rounded-md border border-border/60 bg-muted/20 px-3 py-2">
        <label className="flex flex-1 cursor-pointer select-none items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={skill.auto_bind ?? false}
            disabled={busy}
            onChange={(e) => void toggle(e.target.checked)}
            className="size-3.5 cursor-pointer rounded border-border accent-primary"
            data-testid="skill-auto-bind-toggle"
          />
          <div className="flex flex-col gap-0.5">
            <span className="font-medium text-foreground/90">{t('defaultLinkAll')}</span>
            <span className="text-[11px] text-muted-foreground">
              {skill.auto_bind ? t('defaultLinkAllEnabled') : t('defaultLinkAllDisabled')}
            </span>
          </div>
        </label>
        {busy && <span className="text-[11px] text-muted-foreground">{t('saving')}</span>}
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  )
}
