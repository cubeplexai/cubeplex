'use client'

import { useState } from 'react'
import { ArrowUpCircle, Download, Trash2 } from 'lucide-react'
import type { SkillDetail } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { jsonHeaders, csrfHeaders, readApiError } from '@/lib/csrf'

interface OrgInstallActionsProps {
  skill: SkillDetail
  onActionDone: () => void
}

type Action = 'install' | 'upgrade' | 'uninstall' | null

export function OrgInstallActions({ skill, onActionDone }: OrgInstallActionsProps) {
  const [busy, setBusy] = useState<Action>(null)
  const [error, setError] = useState<string | null>(null)

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

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        {skill.install_state === 'uninstalled' && (
          <Button
            size="sm"
            disabled={busy !== null}
            onClick={() => void run('install', () => install(skill.current_version))}
            data-testid="skill-install-button"
          >
            <Download className="size-3.5" />
            {busy === 'install' ? '安装中…' : `安装 v${skill.current_version}`}
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
            {busy === 'upgrade' ? '升级中…' : `升级到 v${skill.current_version}`}
          </Button>
        )}

        {(skill.install_state === 'installed' || skill.install_state === 'update_available') && (
          <Button
            size="sm"
            variant="destructive"
            disabled={busy !== null}
            onClick={() => void run('uninstall', uninstall)}
            data-testid="skill-uninstall-button"
          >
            <Trash2 className="size-3.5" />
            {busy === 'uninstall' ? '卸载中…' : '卸载'}
          </Button>
        )}
      </div>

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1.5 text-xs text-destructive">
          {error}
        </div>
      )}
    </div>
  )
}
