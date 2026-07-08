'use client'

import { useTranslations } from 'next-intl'
import { adminInvokeTool, type ApiClient, type ToolInvokeResult } from '@cubebox/core'

import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

import { TryItForm } from './TryItForm'

export interface AdminTryItViewProps {
  connectorId: string
  toolName: string
  inputSchema: Record<string, unknown> | null
  client: ApiClient
  /** Panel lens workspace id (the workspace this admin detail panel
   * is currently viewing). Used as the identity-token `ws` claim for
   * auth='none' installs only. */
  wsId: string | null
  /** Workspace options for the scoped-credential picker. */
  adminWorkspaceOptions?: Array<{ id: string; name: string }>
  /** The currently picked workspace lens when policy is workspace/user. */
  scopedAdminWorkspaceId?: string | null
  onScopedWorkspaceChange?: (wsId: string) => void
  /** True when the effective credential policy is workspace or user. */
  requiresWorkspacePicker?: boolean
  /** Connector auth_method; controls whether the lens wsId is sent for
   * non-picker installs (only auth='none' needs it). */
  adminAuthMethod?: 'oauth' | 'static' | 'none'
}

export function AdminTryItView({
  connectorId,
  toolName,
  inputSchema,
  client,
  wsId,
  adminWorkspaceOptions,
  scopedAdminWorkspaceId,
  onScopedWorkspaceChange,
  requiresWorkspacePicker,
  adminAuthMethod,
}: AdminTryItViewProps) {
  const t = useTranslations('mcp.tools.detail.tryit')

  const onRun = async (args: Record<string, unknown>): Promise<ToolInvokeResult> => {
    // Lens precedence:
    //   1. requiresWorkspacePicker (effective policy is workspace/user)
    //      → use explicit picker selection.
    //   2. auth='none' → use the panel lens wsId so the identity
    //      token carries a real `ws` claim instead of empty.
    //   3. Otherwise (org-policy install) → null lens, so the backend
    //      resolves the org grant without rejecting installs not
    //      enabled in the panel workspace.
    let lens: string | null
    if (requiresWorkspacePicker) {
      lens = scopedAdminWorkspaceId ?? null
    } else if (adminAuthMethod === 'none') {
      lens = wsId ?? null
    } else {
      lens = null
    }
    return adminInvokeTool(client, connectorId, toolName, args, lens)
  }

  const showPicker = requiresWorkspacePicker === true && !!adminWorkspaceOptions
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

  const runDisabled = requiresWorkspacePicker === true && !scopedAdminWorkspaceId

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
