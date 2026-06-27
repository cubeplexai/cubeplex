import { use } from 'react'
import { ImPanel } from '@/components/workspace-settings/ImPanel'
import { MemoryPanel } from '@/components/workspace-settings/MemoryPanel'
import { MembersPanel } from '@/components/workspace-settings/MembersPanel'
import { PersonaEditor } from '@/components/workspace-settings/PersonaEditor'
import { SandboxesPanel } from '@/components/workspace-settings/SandboxesPanel'
import { SandboxEnvPanel } from '@/components/workspace-settings/SandboxEnvPanel'
import { SettingsTabs } from '@/components/workspace-settings/SettingsTabs'
import { SharesPanel } from '@/components/workspace-settings/SharesPanel'

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
      <div className="flex flex-1 flex-col overflow-hidden">
        {tab === 'workspace' && <PersonaEditor wsId={wsId} />}
        {tab === 'im' && <ImPanel wsId={wsId} />}
        {tab === 'memory' && <MemoryPanel wsId={wsId} />}
        {tab === 'sandboxEnv' && <SandboxEnvPanel wsId={wsId} />}
        {tab === 'members' && <MembersPanel wsId={wsId} />}
        {tab === 'shares' && <SharesPanel wsId={wsId} />}
        {tab === 'sandboxes' && <SandboxesPanel wsId={wsId} />}
      </div>
    </div>
  )
}
