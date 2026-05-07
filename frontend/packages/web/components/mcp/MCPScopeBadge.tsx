'use client'

import type { MCPCredentialScope } from '@cubebox/core'
import type { ComponentProps } from 'react'
import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'

const variants: Record<MCPCredentialScope, ComponentProps<typeof Badge>['variant']> = {
  org: 'default',
  workspace: 'secondary',
  user: 'outline',
  none: 'ghost',
}

export function MCPScopeBadge({ scope }: { scope: MCPCredentialScope }) {
  const t = useTranslations('mcp.scopeBadge')
  return <Badge variant={variants[scope]}>{t(scope)}</Badge>
}
