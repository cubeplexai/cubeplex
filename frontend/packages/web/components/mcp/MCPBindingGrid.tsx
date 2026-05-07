'use client'

import { useEffect, useState } from 'react'
import type { ApiClient, WorkspaceBinding } from '@cubebox/core'
import { adminGetBindings, adminPutBindings } from '@cubebox/core'
import { Loader2 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
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

export interface MCPBindingGridProps {
  client: ApiClient
  serverId: string
  workspaces: MCPWorkspaceOption[]
}

function bindingsToMap(bindings: WorkspaceBinding[]): Record<string, boolean> {
  return Object.fromEntries(bindings.map((binding) => [binding.workspace_id, binding.enabled]))
}

export function MCPBindingGrid({ client, serverId, workspaces }: MCPBindingGridProps) {
  const t = useTranslations('mcp.bindings')
  const [bindings, setBindings] = useState<Record<string, boolean>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true

    async function loadBindings(): Promise<void> {
      setLoading(true)
      setError(null)
      try {
        const loaded = await adminGetBindings(client, serverId)
        if (!active) return
        setBindings(bindingsToMap(loaded))
        setDirty(false)
      } catch (err) {
        if (!active) return
        setError((err as Error).message)
      } finally {
        if (active) setLoading(false)
      }
    }

    void loadBindings()

    return () => {
      active = false
    }
  }, [client, serverId])

  function toggleWorkspace(workspaceId: string, enabled: boolean): void {
    setBindings((current) => ({ ...current, [workspaceId]: enabled }))
    setDirty(true)
  }

  function setAll(enabled: boolean): void {
    setBindings(Object.fromEntries(workspaces.map((workspace) => [workspace.id, enabled])))
    setDirty(true)
  }

  async function saveBindings(): Promise<void> {
    setSaving(true)
    setError(null)
    try {
      const payload = workspaces.map((workspace) => ({
        workspace_id: workspace.id,
        enabled: bindings[workspace.id] ?? false,
      }))
      const saved = await adminPutBindings(client, serverId, payload)
      setBindings(bindingsToMap(saved))
      setDirty(false)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const enabledCount = workspaces.filter((workspace) => bindings[workspace.id]).length

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
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={loading || saving || workspaces.length === 0}
              onClick={() => setAll(true)}
            >
              {t('enableAll')}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={loading || saving || workspaces.length === 0}
              onClick={() => setAll(false)}
            >
              {t('disableAll')}
            </Button>
          </div>
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
                workspaces.map((workspace) => (
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
                        checked={bindings[workspace.id] ?? false}
                        disabled={loading || saving}
                        onCheckedChange={(checked) => toggleWorkspace(workspace.id, checked)}
                      />
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </CardContent>
      <CardFooter className="justify-end gap-2">
        <Button
          type="button"
          disabled={!dirty || loading || saving}
          onClick={() => void saveBindings()}
        >
          {saving && <Loader2 data-icon="inline-start" className="animate-spin" />}
          {t('save')}
        </Button>
      </CardFooter>
    </Card>
  )
}
