import {
  useArtifactStore,
  useConversationStore,
  useMcpToolRegistryStore,
  usePanelStore,
  useTopicStore,
  useWorkspaceSettingsStore,
} from '@cubeplex/core'

/** Drop client state that must not bleed across workspaces. */
export function resetWorkspaceScopedClientState(): void {
  useConversationStore.setState({ conversations: [], activeId: null })
  useTopicStore.setState({ topics: [], topicParticipants: {} })
  useArtifactStore.setState({ artifacts: {} })
  useWorkspaceSettingsStore.setState({
    agentConfig: null,
    skills: null,
    mcpEffectiveConnectors: null,
    loading: false,
    error: null,
  })
  useMcpToolRegistryStore.setState({ byWorkspace: {}, loading: {} })
  // Panel content is conversation/workspace scoped. Leaving it open after
  // a switch renders an empty rail with no close control (ArtifactPanel
  // returns null once artifacts were cleared above).
  usePanelStore.getState().close()
}
