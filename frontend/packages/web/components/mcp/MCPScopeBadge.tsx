'use client'

import { useTranslations } from 'next-intl'
import type { MCPTemplateScope } from '@cubebox/core'
import type { ComponentProps } from 'react'

import { Badge } from '@/components/ui/badge'

const SCOPE_VARIANTS: Record<MCPTemplateScope, ComponentProps<typeof Badge>['variant']> = {
  global: 'default',
  org: 'secondary',
  workspace: 'outline',
}

export function MCPScopeBadge({ scope }: { scope: MCPTemplateScope }) {
  const t = useTranslations('mcpAdmin')
  const label =
    scope === 'global'
      ? t('templateScopeGlobal')
      : scope === 'org'
        ? t('templateScopeOrg')
        : t('templateScopeWorkspace')
  return <Badge variant={SCOPE_VARIANTS[scope]}>{label}</Badge>
}
