'use client'

import { useTranslations } from 'next-intl'
import type { MCPTemplateScope } from '@cubeplex/core'
import type { ComponentProps } from 'react'

import { Badge } from '@/components/ui/badge'

const SCOPE_VARIANTS: Record<MCPTemplateScope, ComponentProps<typeof Badge>['variant']> = {
  global: 'secondary',
  org: 'secondary',
  workspace: 'outline',
}

// Subtle brand tint for the global (official catalog) scope — matches the
// design system's line-tab active state (bg-primary/10 text-primary), so it
// keeps brand recognition without shouting in dark mode.
const GLOBAL_CLASS = 'bg-primary/10 text-primary dark:bg-primary/15'

export function MCPScopeBadge({ scope }: { scope: MCPTemplateScope }) {
  const t = useTranslations('mcpAdmin')
  const label =
    scope === 'global'
      ? t('templateScopeGlobal')
      : scope === 'org'
        ? t('templateScopeOrg')
        : t('templateScopeWorkspace')
  return (
    <Badge
      variant={SCOPE_VARIANTS[scope]}
      className={scope === 'global' ? GLOBAL_CLASS : undefined}
    >
      {label}
    </Badge>
  )
}
