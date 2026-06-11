import { use } from 'react'
import { MembersPanel } from '@/components/workspace-settings/MembersPanel'
import { PersonaEditor } from '@/components/workspace-settings/PersonaEditor'
import { SettingsTabs } from '@/components/workspace-settings/SettingsTabs'
import { SharesPanel } from '@/components/workspace-settings/SharesPanel'
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
    <div className="flex flex-1 flex-col overflow-hidden h-full">
      <SettingsTabs wsId={wsId} active={tab} />
      <div className="flex flex-1 overflow-hidden">
        {tab === 'workspace' && <PersonaEditor wsId={wsId} />}
        {tab === 'skills' && <SkillsPanel wsId={wsId} />}
        {tab === 'mcp' && <McpPanel wsId={wsId} />}
        {tab === 'members' && <MembersPanel wsId={wsId} />}
        {tab === 'shares' && <SharesPanel wsId={wsId} />}
      </div>
    </div>
  )
}
