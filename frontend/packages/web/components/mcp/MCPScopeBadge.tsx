import type { MCPCredentialScope } from '@cubebox/core'
import type { ComponentProps } from 'react'

import { Badge } from '@/components/ui/badge'

const variants: Record<MCPCredentialScope, ComponentProps<typeof Badge>['variant']> = {
  org: 'default',
  workspace: 'secondary',
  user: 'outline',
  none: 'ghost',
}

const labels: Record<MCPCredentialScope, string> = {
  org: 'Org shared',
  workspace: 'Workspace shared',
  user: 'Per user',
  none: 'Identity passthrough',
}

export function MCPScopeBadge({ scope }: { scope: MCPCredentialScope }) {
  return <Badge variant={variants[scope]}>{labels[scope]}</Badge>
}
