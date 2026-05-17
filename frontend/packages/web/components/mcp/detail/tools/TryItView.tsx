'use client'

import { useTranslations } from 'next-intl'
import { adminInvokeTool, wsInvokeTool, type ApiClient, type ToolInvokeResult } from '@cubebox/core'

import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { SchemaNode } from '@/lib/jsonSchemaTypes'

import { TryItForm } from './TryItForm'

export interface TryItViewProps {
  toolName: string
  schema: SchemaNode | null | undefined
  installId: string
  client: ApiClient
  surface: 'admin' | 'ws'
  /** For workspace surface, this is the active workspace id. For admin, the lens ws id (optional). */
  wsId: string | null
  /** When surface='admin' and the connector requires scoped credentials, list of selectable workspaces. */
  adminWorkspaceOptions?: Array<{ id: string; name: string }>
  /** When surface='admin' + scoped policy, the user-selected workspace lens. */
  scopedAdminWorkspaceId?: string | null
  onScopedWorkspaceChange?: (wsId: string) => void
  /** True iff the admin connector's required grant scope is workspace or user. */
  requiresWorkspacePicker?: boolean
  /** Connector's auth_method. Used by admin Try It to decide whether
   * to send the panel lens wsId on non-picker installs: auth='none'
   * needs the lens for identity-token `ws` claim, while other
   * auth methods with org-policy must send null lens to avoid the
   * backend rejecting installs not enabled in that workspace. */
  adminAuthMethod?: 'oauth' | 'static' | 'none'
}

export function TryItView({
  toolName,
  schema,
  installId,
  client,
  surface,
  wsId,
  adminWorkspaceOptions,
  scopedAdminWorkspaceId,
  onScopedWorkspaceChange,
  requiresWorkspacePicker,
  adminAuthMethod,
}: TryItViewProps) {
  const t = useTranslations('mcp.tools.detail.tryit')
  const inputSchema = (schema ?? null) as Record<string, unknown> | null

  const onRun = async (args: Record<string, unknown>): Promise<ToolInvokeResult> => {
    if (surface === 'admin') {
      // Lens precedence:
      //   1. requiresWorkspacePicker (effective policy is
      //      workspace/user) → use explicit picker selection.
      //   2. auth='none' → use the panel lens wsId so the identity
      //      token carries a real `ws` claim instead of empty.
      //   3. Otherwise (org-policy install) → null lens, so the
      //      backend resolves the org grant without rejecting
      //      installs not enabled in the panel workspace
      //      (auto_enable.mode='none' / disabled there).
      let lens: string | null
      if (requiresWorkspacePicker) {
        lens = scopedAdminWorkspaceId ?? null
      } else if (adminAuthMethod === 'none') {
        lens = wsId ?? null
      } else {
        lens = null
      }
      return adminInvokeTool(client, installId, toolName, args, lens)
    }
    if (!wsId) throw new Error('workspace id missing')
    return wsInvokeTool(client, wsId, installId, toolName, args)
  }

  const showPicker =
    surface === 'admin' && requiresWorkspacePicker === true && !!adminWorkspaceOptions
  const picker = showPicker ? (
    <div className="flex flex-col gap-1.5">
      <Label className="text-sm">{t('workspaceLensLabel')}</Label>
      <Select
        value={scopedAdminWorkspaceId ?? undefined}
        onValueChange={(v) => {
          if (v) onScopedWorkspaceChange?.(v)
        }}
      >
        <SelectTrigger>
          <SelectValue placeholder={t('workspaceLensPlaceholder')} />
        </SelectTrigger>
        <SelectContent>
          {adminWorkspaceOptions!.map((w) => (
            <SelectItem key={w.id} value={w.id}>
              {w.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <p className="text-xs text-muted-foreground">{t('workspaceLensHelp')}</p>
    </div>
  ) : null

  const runDisabled =
    surface === 'admin' && requiresWorkspacePicker === true && !scopedAdminWorkspaceId

  return (
    <TryItForm
      toolName={toolName}
      inputSchema={inputSchema}
      onRun={onRun}
      runDisabled={runDisabled}
      prefix={picker}
    />
  )
}
