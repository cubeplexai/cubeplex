import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ImAccount, ImBotSettings, Workspace } from '@cubeplex/core'

const mocks = vi.hoisted(() => ({
  createTopic: vi.fn(),
  upgradeConversationToTopic: vi.fn(),
  fetchTopicList: vi.fn().mockResolvedValue(undefined),
  fetchConversationList: vi.fn().mockResolvedValue(undefined),
  loadWsMembers: vi.fn().mockResolvedValue(undefined),
  wsGetImBotSettings: vi.fn(),
  wsUpdateImBotSettings: vi.fn(),
  wsListIdentityLinks: vi.fn().mockResolvedValue({ links: [] }),
  push: vi.fn(),
  refresh: vi.fn(),
  client: { baseUrl: '', setWorkspaceId: vi.fn() },
}))

vi.mock('@cubeplex/core', async () => {
  const { create } = await import('zustand')
  const useWorkspaceStore = create(() => ({
    workspaces: [] as Workspace[],
  }))
  const useAuthStore = create(() => ({
    user: { id: 'u-1' },
  }))
  const useMemberStore = create(() => ({
    wsMembers: [],
    loadWsMembers: mocks.loadWsMembers,
  }))
  const useTopicStore = create(() => ({
    create: mocks.createTopic,
    upgradeConversationToTopic: mocks.upgradeConversationToTopic,
    fetchList: mocks.fetchTopicList,
  }))
  const useConversationStore = create(() => ({
    fetchList: mocks.fetchConversationList,
  }))
  return {
    ApiError: class ApiError extends Error {
      status = 400
    },
    createApiClient: () => mocks.client,
    useWorkspaceStore,
    useAuthStore,
    useMemberStore,
    useTopicStore,
    useConversationStore,
    wsGetImBotSettings: mocks.wsGetImBotSettings,
    wsUpdateImBotSettings: mocks.wsUpdateImBotSettings,
    wsListIdentityLinks: mocks.wsListIdentityLinks,
  }
})

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mocks.push, refresh: mocks.refresh }),
}))

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

import { useWorkspaceStore } from '@cubeplex/core'
import { CreateGroupChatDialog } from '@/components/dialogs/CreateGroupChatDialog'
import { UpgradeToTopicDialog } from '@/components/dialogs/UpgradeToTopicDialog'
import { ImAccountDetailPanel } from '@/components/im/ImAccountDetailPanel'

const sampleAccount: ImAccount = {
  id: 'im-1',
  platform: 'slack',
  external_account_id: 'T123',
  workspace_id: 'ws-shared',
  acting_user_id: 'u-1',
  delivery_mode: 'stream',
  enabled: true,
  bot_app_name: 'HelperBot',
  bot_avatar_url: null,
  runtime: {
    connection_state: 'connected',
    last_inbound_at: null,
    bot_open_id: 'B123',
    pending_queue: 0,
    matched_24h: 0,
    rejected_24h: 0,
  },
}

describe('Workspace shared-sandbox mode in Topic & IM controls', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useWorkspaceStore.setState({
      workspaces: [
        {
          id: 'ws-shared',
          name: 'Shared Team',
          org_id: 'org-1',
          role: 'admin',
          sandbox_mode: 'workspace',
        },
      ],
    })
  })

  it('CreateGroupChatDialog hides sandbox_mode selector and omits sandbox_mode from payload in workspace mode', async () => {
    mocks.createTopic.mockResolvedValue({ topicId: 't-1', conversationId: 'c-1' })

    render(<CreateGroupChatDialog wsId="ws-shared" open={true} onOpenChange={vi.fn()} />)

    expect(screen.queryByText('sandboxLabel')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('titleLabel'), { target: { value: 'Release Sync' } })
    fireEvent.click(screen.getByRole('button', { name: 'create' }))

    await waitFor(() =>
      expect(mocks.createTopic).toHaveBeenCalledWith(mocks.client, {
        title: 'Release Sync',
        member_user_ids: [],
      }),
    )
  })

  it('UpgradeToTopicDialog hides sandbox_mode selector and omits sandbox_mode from payload in workspace mode', async () => {
    mocks.upgradeConversationToTopic.mockResolvedValue({ topicId: 't-2', conversationId: 'c-2' })

    render(
      <UpgradeToTopicDialog
        wsId="ws-shared"
        conversationId="c-2"
        initialTitle="Incident"
        open={true}
        onOpenChange={vi.fn()}
      />,
    )

    expect(screen.queryByText('sandboxLabel')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'upgrade' }))

    await waitFor(() =>
      expect(mocks.upgradeConversationToTopic).toHaveBeenCalledWith(mocks.client, 'c-2', {
        title: 'Incident',
        member_user_ids: [],
      }),
    )
  })

  it('ImAccountDetailPanel hides shared sandbox selector, displays workspaceShared summary, and saves sandbox_mode=null', async () => {
    const initialSettings: ImBotSettings = {
      routing_mode: 'isolated',
      topic_mode: 'topic',
      sandbox_mode: null,
    }
    mocks.wsGetImBotSettings.mockResolvedValue(initialSettings)
    mocks.wsUpdateImBotSettings.mockImplementation(
      async (_c: unknown, _ws: string, _id: string, body: ImBotSettings) => body,
    )

    render(
      <ImAccountDetailPanel
        account={sampleAccount}
        scope="workspace"
        onDisable={vi.fn()}
        onEnable={vi.fn()}
        onDelete={vi.fn()}
      />,
    )

    await waitFor(() =>
      expect(
        screen.getByText('botSettings.summary.group.sandbox.workspaceShared'),
      ).toBeInTheDocument(),
    )

    // Switch routing_mode from isolated -> shared
    const routingTrigger = document.getElementById('im-routing-mode')!
    fireEvent.click(routingTrigger)
    const sharedOpt = await screen.findByRole('option', { name: 'botSettings.routing.shared' })
    act(() => {
      sharedOpt.focus()
    })
    fireEvent.keyDown(sharedOpt, { key: 'Enter' })

    // Even in shared routing, #im-sandbox-mode remains hidden and saving sends sandbox_mode: null
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'botSettings.action.save' })).toBeEnabled(),
    )
    expect(document.getElementById('im-sandbox-mode')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'botSettings.action.save' }))

    await waitFor(() =>
      expect(mocks.wsUpdateImBotSettings).toHaveBeenCalledWith(mocks.client, 'ws-shared', 'im-1', {
        routing_mode: 'shared',
        topic_mode: 'topic',
        sandbox_mode: null,
      }),
    )
  })
})
