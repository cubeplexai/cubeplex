'use client'

import { useTranslations } from 'next-intl'
import type { AdminCatalogRow } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { MCPScopeBadge } from './MCPScopeBadge'

interface MCPCatalogListProps {
  rows: AdminCatalogRow[]
  loading: boolean
  selectedTemplateId: string | null
  onSelect: (templateId: string) => void
}

function OrgGrantDot({ status }: { status: 'valid' | 'expired' | null }) {
  const t = useTranslations('mcpAdmin')
  if (status === null) return null
  return (
    <span
      title={status === 'valid' ? t('orgGrantValid') : t('orgGrantExpired')}
      className={cn(
        'inline-block size-2 shrink-0 rounded-full',
        status === 'valid' ? 'bg-success-solid' : 'bg-danger-solid',
      )}
      aria-label={status === 'valid' ? t('orgGrantDotAriaValid') : t('orgGrantDotAriaExpired')}
    />
  )
}

function CatalogRow({
  row,
  active,
  onSelect,
}: {
  row: AdminCatalogRow
  active: boolean
  onSelect: () => void
}) {
  const t = useTranslations('mcpAdmin')
  const {
    template,
    connector,
    disabled,
    in_use,
    needs_attention,
    enabled_workspace_count,
    eligible_workspace_count,
    org_grant_status,
  } = row

  return (
    <button
      type="button"
      onClick={onSelect}
      data-testid={`catalog-row-${template.template_id}`}
      aria-current={active ? 'true' : undefined}
      className={cn(
        'group flex w-full flex-col gap-1.5 rounded-lg border p-3 text-left transition-all',
        active
          ? 'border-primary/40 bg-primary/5 shadow-sm'
          : 'border-border/70 bg-card/40 hover:border-border hover:bg-accent/40',
      )}
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate text-sm font-semibold">{template.name}</span>
        {template.provider && template.provider.toLowerCase() !== template.name.toLowerCase() ? (
          <Badge variant="outline" className="shrink-0 text-[10px]">
            {template.provider}
          </Badge>
        ) : null}
        <OrgGrantDot status={org_grant_status} />
        {disabled ? (
          <Badge variant="destructive" className="shrink-0 text-[10px]">
            {t('disabledBadge')}
          </Badge>
        ) : null}
        {needs_attention && !disabled ? (
          <Badge variant="secondary" className="shrink-0 text-[10px] text-warning-fg">
            {t('needsAttentionBadge')}
          </Badge>
        ) : null}
      </div>

      {template.description ? (
        <p className="line-clamp-1 text-xs text-muted-foreground">{template.description}</p>
      ) : null}

      <div className="flex flex-wrap items-center gap-1 pt-0.5">
        <MCPScopeBadge scope={template.scope} />
        {connector ? (
          <span
            className={cn(
              'text-[10px] tabular-nums',
              in_use ? 'text-success-fg' : 'text-muted-foreground',
            )}
            title={t('workspaceRatio', {
              enabled: enabled_workspace_count,
              eligible: eligible_workspace_count,
            })}
          >
            {enabled_workspace_count}/{eligible_workspace_count}
          </span>
        ) : (
          <Badge variant="ghost" className="text-[10px]">
            {t('notInstalledBadge')}
          </Badge>
        )}
      </div>
    </button>
  )
}

export function MCPCatalogList({
  rows,
  loading,
  selectedTemplateId,
  onSelect,
}: MCPCatalogListProps) {
  const t = useTranslations('mcpAdmin')

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center py-10 text-sm text-muted-foreground">
        {t('catalogLoading')}
      </div>
    )
  }

  if (rows.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-1 py-10 text-center">
        <p className="text-sm font-medium text-foreground">{t('catalogNoConnectors')}</p>
        <p className="text-xs text-muted-foreground">{t('catalogNoConnectorsHint')}</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-1.5 p-3">
      {rows.map((row) => (
        <CatalogRow
          key={row.template.template_id}
          row={row}
          active={row.template.template_id === selectedTemplateId}
          onSelect={() => onSelect(row.template.template_id)}
        />
      ))}
    </div>
  )
}
