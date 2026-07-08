import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import en from '../../messages/en.json'
import { McpPanel } from '../../components/workspace-settings/McpPanel'

const coreMocks = vi.hoisted(() => ({
  adminPromoteToOrg: vi.fn(),
  createApiClient: vi.fn(() => ({ setWorkspaceId: vi.fn() })),
  useOrgAdminFlag: vi.fn(() => false),
  useWorkspaceStore: vi.fn((selector: (state: unknown) => unknown) =>
    selector({ workspaces: [{ id: 'ws_1', org_id: 'org_1', role: 'admin' }] }),
  ),
  wsDeleteInstall: vi.fn(),
  wsListAvailable: vi.fn(),
  wsListEffectiveConnectors: vi.fn(),
  wsPatchConnectorState: vi.fn(),
  wsRefreshDiscovery: vi.fn(),
}))

vi.mock('@cubebox/core', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@cubebox/core')>()
  return {
    ...actual,
    adminPromoteToOrg: coreMocks.adminPromoteToOrg,
    createApiClient: coreMocks.createApiClient,
    useOrgAdminFlag: coreMocks.useOrgAdminFlag,
    useWorkspaceStore: coreMocks.useWorkspaceStore,
    wsDeleteInstall: coreMocks.wsDeleteInstall,
    wsListAvailable: coreMocks.wsListAvailable,
    wsListEffectiveConnectors: coreMocks.wsListEffectiveConnectors,
    wsPatchConnectorState: coreMocks.wsPatchConnectorState,
    wsRefreshDiscovery: coreMocks.wsRefreshDiscovery,
  }
})

function renderWithIntl(node: React.ReactNode): ReturnType<typeof render> {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      {node}
    </NextIntlClientProvider>,
  )
}

function workspaceConnector() {
  return {
    template: {
      template_id: 'mcptpl_atlassian',
      slug: 'atlassian',
      name: 'Atlassian',
      provider: 'Atlassian',
      description: 'Atlassian Rovo MCP server',
      supported_auth_methods: ['oauth', 'static'],
    },
    install: {
      connector_id: 'mcins_atlassian',
      template_id: 'mcptpl_atlassian',
      install_scope: 'workspace',
      workspace_id: 'ws_1',
      name: 'Atlassian',
      auth_method: 'static',
      auth_status: 'missing',
      discovery_status: 'pending',
      install_state: 'active',
      tool_count: 0,
      tools: [],
      tool_citations: {},
      last_error: null,
      auto_enroll_new_workspaces: false,
    },
    workspace_state: { workspace_id: 'ws_1', connector_id: 'mcins_atlassian', enabled: true },
    credential_policy: 'workspace',
    required_grant_scope: 'workspace',
    credential_availability: 'missing',
    credential_source: null,
    usable: false,
    reason: 'missing_workspace_grant',
  }
}

describe('McpPanel workspace installs', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    })
    coreMocks.wsDeleteInstall.mockReset()
    coreMocks.wsListAvailable.mockReset()
    coreMocks.wsListEffectiveConnectors.mockReset()
    coreMocks.wsPatchConnectorState.mockReset()
    coreMocks.wsRefreshDiscovery.mockReset()
    coreMocks.wsListAvailable.mockResolvedValue({ items: [] })
    coreMocks.wsListEffectiveConnectors.mockResolvedValue({ items: [workspaceConnector()] })
  })

  it('uninstalls a workspace-private connector so it can be reinstalled from the template', async () => {
    coreMocks.wsDeleteInstall.mockResolvedValue(undefined)
    renderWithIntl(<McpPanel wsId="ws_1" />)

    fireEvent.click(await screen.findByTestId('ws-connector-row-mcins_atlassian'))
    fireEvent.click(await screen.findByRole('button', { name: 'Uninstall' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm uninstall' }))

    expect(coreMocks.wsDeleteInstall).toHaveBeenCalledWith(
      expect.anything(),
      'ws_1',
      'mcins_atlassian',
    )
    await waitFor(() => {
      expect(coreMocks.wsListEffectiveConnectors).toHaveBeenCalledTimes(2)
    })
  })
})
