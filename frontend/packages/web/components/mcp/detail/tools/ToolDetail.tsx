'use client'

import type { ApiClient, MCPToolEntry } from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type { SchemaNode } from '@/lib/jsonSchemaTypes'

import { JsonView } from './JsonView'
import { SchemaView } from './SchemaView'
import { TryItView } from './TryItView'

export type ToolDetailView = 'schema' | 'tryit' | 'json'

export interface ToolDetailProps {
  tool: MCPToolEntry
  view: ToolDetailView
  onViewChange: (view: ToolDetailView) => void
  installId: string
  client: ApiClient
  surface: 'admin' | 'ws'
  wsId: string | null
  adminWorkspaceOptions?: Array<{ id: string; name: string }>
  scopedAdminWorkspaceId?: string | null
  onScopedWorkspaceChange?: (wsId: string) => void
  requiresWorkspacePicker?: boolean
  adminAuthMethod?: 'oauth' | 'static' | 'none'
}

export function ToolDetail({
  tool,
  view,
  onViewChange,
  installId,
  client,
  surface,
  wsId,
  adminWorkspaceOptions,
  scopedAdminWorkspaceId,
  onScopedWorkspaceChange,
  requiresWorkspacePicker,
  adminAuthMethod,
}: ToolDetailProps) {
  const t = useTranslations('mcp.tools.detail')
  const schema = (tool.input_schema as SchemaNode | null) ?? null

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 className="font-mono text-lg font-semibold">{tool.name}</h2>
        {tool.description ? (
          <p className="text-sm text-muted-foreground">{tool.description}</p>
        ) : null}
      </div>

      <Tabs value={view} onValueChange={(v) => onViewChange(v as ToolDetailView)}>
        <TabsList>
          <TabsTrigger value="schema">{t('viewSchema')}</TabsTrigger>
          <TabsTrigger value="tryit">{t('viewTryIt')}</TabsTrigger>
          <TabsTrigger value="json">{t('viewJson')}</TabsTrigger>
        </TabsList>
        <TabsContent value="schema" className="mt-4">
          <SchemaView schema={schema} />
        </TabsContent>
        <TabsContent value="tryit" className="mt-4">
          <TryItView
            key={tool.name}
            toolName={tool.name}
            schema={schema}
            installId={installId}
            client={client}
            surface={surface}
            wsId={wsId}
            adminWorkspaceOptions={adminWorkspaceOptions}
            scopedAdminWorkspaceId={scopedAdminWorkspaceId}
            onScopedWorkspaceChange={onScopedWorkspaceChange}
            requiresWorkspacePicker={requiresWorkspacePicker}
            adminAuthMethod={adminAuthMethod}
          />
        </TabsContent>
        <TabsContent value="json" className="mt-4">
          <JsonView schema={schema} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
