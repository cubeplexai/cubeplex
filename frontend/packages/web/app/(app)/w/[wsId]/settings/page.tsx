import { use } from 'react'
import { PersonaEditor } from '@/components/workspace-settings/PersonaEditor'
import { SkillsPanel } from '@/components/workspace-settings/SkillsPanel'
import { McpPanel } from '@/components/workspace-settings/McpPanel'

interface SettingsPageProps {
  params: Promise<{ wsId: string }>
  searchParams: Promise<{ tab?: string; sub?: string }>
}

export default function WorkspaceSettingsPage({
  params,
  searchParams,
}: SettingsPageProps): React.ReactElement {
  const { wsId } = use(params)
  const { tab = 'workspace' } = use(searchParams)

  return (
    <div className="flex flex-1 overflow-hidden h-full">
      {tab === 'workspace' && <PersonaEditor wsId={wsId} />}
      {tab === 'skills' && <SkillsPanel wsId={wsId} />}
      {tab === 'mcp' && <McpPanel wsId={wsId} />}
    </div>
  )
}
