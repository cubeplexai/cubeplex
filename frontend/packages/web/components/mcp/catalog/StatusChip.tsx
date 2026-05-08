'use client'

import type { MCPCatalogConnector } from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { cn } from '@/lib/utils'

export type CatalogStatus = 'not_installed' | 'available_org_wide' | 'installed_for_you'

export function deriveCatalogStatus(
  connector: MCPCatalogConnector,
  mode: 'admin' | 'workspace',
): CatalogStatus {
  if (mode === 'admin') {
    return connector.org_install_id ? 'available_org_wide' : 'not_installed'
  }
  // workspace view
  if (connector.user_install_id) {
    return 'installed_for_you'
  }
  if (connector.workspace_visible) {
    return 'available_org_wide'
  }
  return 'not_installed'
}

export interface StatusChipProps {
  status: CatalogStatus
  className?: string
}

// TODO(catalog-status): "OAuth required" / "Auth expired" chips depend on the
// catalog API exposing a per-row `authed` flag. Add once Phase 7+ surfaces it.
export function StatusChip({ status, className }: StatusChipProps) {
  const t = useTranslations('mcpCatalog.status')
  const styles: Record<CatalogStatus, string> = {
    not_installed: 'bg-muted text-muted-foreground border-border',
    available_org_wide:
      'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950/40 dark:text-blue-300 dark:border-blue-900',
    installed_for_you:
      'bg-green-50 text-green-700 border-green-200 dark:bg-green-950/40 dark:text-green-300 dark:border-green-900',
  }
  const labels: Record<CatalogStatus, string> = {
    not_installed: t('notInstalled'),
    available_org_wide: t('availableOrgWide'),
    installed_for_you: t('installedForYou'),
  }
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium',
        styles[status],
        className,
      )}
    >
      {labels[status]}
    </span>
  )
}
