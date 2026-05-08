'use client'

import { useEffect, useState } from 'react'
import type { ApiClient, WorkspaceOverride } from '@cubebox/core'
import { adminGetOverrides, adminPutOverride } from '@cubebox/core'
import { Loader2 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Switch } from '@/components/ui/switch'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

export interface MCPWorkspaceOption {
  id: string
  name: string
}

export interface MCPOverrideGridProps {
  client: ApiClient
  serverId: string
  workspaces: MCPWorkspaceOption[]
}

function overridesToDisabledMap(overrides: WorkspaceOverride[]): Record<string, boolean> {
  // Backend only stores explicit disable rows; if a row is present and
  // enabled=false, that workspace is opted out. Anything not present
  // inherits the org-wide install (enabled by default).
  return Object.fromEntries(
    overrides
      .filter((override) => !override.enabled)
      .map((override) => [override.workspace_id, true]),
  )
}

export function MCPOverrideGrid({ client, serverId, workspaces }: MCPOverrideGridProps) {
  const t = useTranslations('mcp.overrides')
  const [disabled, setDisabled] = useState<Record<string, boolean>>({})
  const [loading, setLoading] = useState(true)
  const [savingId, setSavingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true

    async function loadOverrides(): Promise<void> {
      setLoading(true)
      setError(null)
      try {
        const loaded = await adminGetOverrides(client, serverId)
        if (!active) return
        setDisabled(overridesToDisabledMap(loaded))
      } catch (err) {
        if (!active) return
        setError((err as Error).message)
      } finally {
        if (active) setLoading(false)
      }
    }

    void loadOverrides()

    return () => {
      active = false
    }
  }, [client, serverId])

  async function toggleWorkspace(workspaceId: string, enabled: boolean): Promise<void> {
    setSavingId(workspaceId)
    setError(null)
    try {
      const saved = await adminPutOverride(client, serverId, {
        workspace_id: workspaceId,
        enabled,
      })
      setDisabled(overridesToDisabledMap(saved))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSavingId(null)
    }
  }

  const enabledCount = workspaces.filter((workspace) => !disabled[workspace.id]).length

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('title')}</CardTitle>
        <CardDescription>{t('description')}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {error && (
          <Alert variant="destructive">
            <AlertTitle>{t('updateFailed')}</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-muted-foreground">
            {t('summary', { enabled: enabledCount, total: workspaces.length })}
          </p>
          {savingId !== null && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
        </div>

        <div className="overflow-hidden rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t('workspace')}</TableHead>
                <TableHead className="w-[120px] text-right">{t('enabled')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {workspaces.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={2} className="text-muted-foreground">
                    {t('noWorkspaces')}
                  </TableCell>
                </TableRow>
              ) : (
                workspaces.map((workspace) => {
                  const isEnabled = !disabled[workspace.id]
                  const isBusy = savingId === workspace.id
                  return (
                    <TableRow key={workspace.id}>
                      <TableCell>
                        <div className="flex flex-col gap-0.5">
                          <span className="font-medium">{workspace.name}</span>
                          <span className="text-xs text-muted-foreground">{workspace.id}</span>
                        </div>
                      </TableCell>
                      <TableCell className="text-right">
                        <Switch
                          aria-label={t('toggleAria', { name: workspace.name })}
                          checked={isEnabled}
                          disabled={loading || isBusy}
                          onCheckedChange={(checked) => void toggleWorkspace(workspace.id, checked)}
                        />
                      </TableCell>
                    </TableRow>
                  )
                })
              )}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  )
}
