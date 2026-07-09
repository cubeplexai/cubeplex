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
  wsCreateInstall: vi.fn(),
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
    wsCreateInstall: coreMocks.wsCreateInstall,
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
    credential_availability_by_scope: {
      org: false,
      workspace: false,
      user: false,
    },
    usable: false,
    reason: 'missing_workspace_grant',
  }
}

function workspaceConnectorWith(params: { connectorId: string; name: string; provider?: string }) {
  const base = workspaceConnector()
  return {
    ...base,
    template: {
      ...base.template,
      name: params.name,
      provider: params.provider ?? params.name,
      description: `${params.name} MCP server`,
    },
    install: {
      ...base.install,
      connector_id: params.connectorId,
      name: params.name,
    },
    workspace_state: {
      ...base.workspace_state,
      connector_id: params.connectorId,
    },
  }
}

function orgCredentialConnector() {
  return {
    ...workspaceConnector(),
    install: {
      ...workspaceConnector().install,
      connector_id: 'mcpco_linear',
      install_scope: 'org',
      workspace_id: null,
      name: 'Linear',
      auth_status: 'pending',
      default_credential_policy: 'org',
    },
    workspace_state: {
      workspace_id: 'ws_1',
      connector_id: 'mcpco_linear',
      enabled: true,
      credential_policy: 'org',
    },
    credential_policy: 'workspace',
    required_grant_scope: 'workspace',
    credential_availability: 'available',
    credential_source: 'workspace',
    credential_availability_by_scope: {
      org: true,
      workspace: true,
      user: true,
    },
    usable: true,
    reason: 'usable',
  }
}

function availableOrgConnector() {
  const connector = orgCredentialConnector()
  return {
    source: 'org_install',
    install: connector.install,
    template: connector.template,
    reason: 'no_state_row',
    credential_availability_by_scope: {
      org: true,
      workspace: false,
      user: false,
    },
  }
}

function availableTemplate() {
  return {
    source: 'template',
    install: null,
    template: {
      template_id: 'mcptpl_custom',
      slug: 'custom',
      name: 'Custom template',
      provider: 'Custom',
      description: 'Template available to this workspace',
      server_url: 'https://template.example.com/mcp',
      transport: 'streamable_http',
      supported_auth_methods: ['static'],
      default_credential_policy: 'workspace',
      static_form_schema: null,
      status: 'active',
    },
    reason: 'not_installed_at_org',
    credential_availability_by_scope: {
      org: false,
      workspace: false,
      user: false,
    },
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
    coreMocks.wsCreateInstall.mockReset()
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

  it('explains credential policies and highlights an available org credential', async () => {
    coreMocks.wsListEffectiveConnectors.mockResolvedValue({ items: [orgCredentialConnector()] })
    renderWithIntl(<McpPanel wsId="ws_1" />)

    fireEvent.click(await screen.findByTestId('ws-connector-row-mcpco_linear'))

    expect(screen.getByText('Org credential available')).toBeInTheDocument()
    expect(
      screen.getByText('Use the organization credential managed by admins.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Store one credential for this workspace.')).toBeInTheDocument()
    expect(screen.getByText('Each user connects their own account.')).toBeInTheDocument()

    const noneButton = screen.getByRole('button', { name: /None/i })
    expect(noneButton).toBeDisabled()
    expect(screen.getAllByText('Only available for no-auth connectors.')).toHaveLength(1)
    expect(screen.getByLabelText('Organization credential available')).toBeInTheDocument()
    expect(screen.getByLabelText('Workspace credential available')).toBeInTheDocument()
    expect(screen.getByLabelText('User credential available')).toBeInTheDocument()
  })

  it('uses the org credential by default when enabling an org connector with saved org creds', async () => {
    coreMocks.wsListEffectiveConnectors.mockResolvedValue({ items: [] })
    coreMocks.wsListAvailable.mockResolvedValue({ items: [availableOrgConnector()] })
    coreMocks.wsPatchConnectorState.mockResolvedValue({
      workspace_id: 'ws_1',
      connector_id: 'mcpco_linear',
      enabled: true,
      credential_policy: 'org',
    })
    renderWithIntl(<McpPanel wsId="ws_1" />)

    fireEvent.click(await screen.findByTestId('ws-available-row-mcpco_linear'))
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }))

    await waitFor(() => {
      expect(coreMocks.wsPatchConnectorState).toHaveBeenCalledWith(
        expect.anything(),
        'ws_1',
        'mcpco_linear',
        { enabled: true, credential_policy: 'org' },
      )
    })
  })

  it('creates a workspace-scoped custom connector from workspace settings', async () => {
    coreMocks.wsListEffectiveConnectors.mockResolvedValue({ items: [] })
    coreMocks.wsListAvailable.mockResolvedValue({ items: [availableTemplate()] })
    coreMocks.wsCreateInstall.mockResolvedValue({
      connector_id: 'mcpco_custom',
      template_id: null,
      install_scope: 'workspace',
      workspace_id: 'ws_1',
      name: 'Internal Search',
      server_url: 'https://search.example.com/mcp',
      transport: 'streamable_http',
      auth_method: 'static',
      default_credential_policy: 'workspace',
      auth_status: 'pending',
      discovery_status: 'not_run',
      install_state: 'active',
      tool_count: 0,
      tools: [],
      tool_citations: {},
      last_error: null,
      auto_enroll_new_workspaces: false,
    })
    renderWithIntl(<McpPanel wsId="ws_1" />)

    fireEvent.click(await screen.findByRole('button', { name: 'Add custom connector' }))
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Internal Search' } })
    fireEvent.change(screen.getByLabelText('Server URL'), {
      target: { value: 'https://search.example.com/mcp' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create server' }))

    await waitFor(() => {
      expect(coreMocks.wsCreateInstall).toHaveBeenCalledWith(
        expect.anything(),
        'ws_1',
        expect.objectContaining({
          template_id: null,
          install_scope: 'workspace',
          auth_method: 'static',
          default_credential_policy: 'workspace',
          name: 'Internal Search',
          server_url: 'https://search.example.com/mcp',
        }),
      )
    })
  })

  it('clears a connector operation error when another connector is selected', async () => {
    coreMocks.wsListEffectiveConnectors.mockResolvedValue({
      items: [
        workspaceConnectorWith({ connectorId: 'mcpco_cloudflare', name: 'Cloudflare API' }),
        workspaceConnectorWith({ connectorId: 'mcpco_linear', name: 'Linear' }),
      ],
    })
    coreMocks.wsRefreshDiscovery.mockRejectedValue(new Error('An unexpected error occurred'))
    renderWithIntl(<McpPanel wsId="ws_1" />)

    fireEvent.click(await screen.findByTestId('ws-connector-row-mcpco_cloudflare'))
    fireEvent.click(screen.getByRole('button', { name: 'Refresh Tools' }))

    expect(await screen.findByText('An unexpected error occurred')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('ws-connector-row-mcpco_linear'))

    await waitFor(() => {
      expect(screen.queryByText('An unexpected error occurred')).not.toBeInTheDocument()
    })
  })
})
