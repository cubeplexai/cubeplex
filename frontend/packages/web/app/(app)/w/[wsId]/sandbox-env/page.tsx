'use client'

import { use } from 'react'
import { SandboxEnvPanel } from '@/components/workspace-settings/SandboxEnvPanel'

interface PageProps {
  params: Promise<{ wsId: string }>
}

export default function WorkspaceSandboxEnvPage({ params }: PageProps): React.ReactElement {
  const { wsId } = use(params)
  return <SandboxEnvPanel wsId={wsId} />
}
