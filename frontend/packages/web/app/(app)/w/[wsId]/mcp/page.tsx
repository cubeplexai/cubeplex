import { use } from 'react'
import { McpPanel } from '@/components/workspace-settings/McpPanel'

interface PageProps {
  params: Promise<{ wsId: string }>
}

export default function WorkspaceMcpPage({ params }: PageProps): React.ReactElement {
  const { wsId } = use(params)
  return <McpPanel wsId={wsId} />
}
