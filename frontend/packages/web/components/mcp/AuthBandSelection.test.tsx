import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { StrictMode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import en from '../../messages/en.json'
import { AdminAuthBand } from './AdminAuthBand'
import { WsAuthBand } from './WsAuthBand'

const coreMocks = vi.hoisted(() => ({
  runOAuthFlow: vi.fn(),
}))

vi.mock('@cubeplex/core', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@cubeplex/core')>()
  return {
    ...actual,
    runOAuthFlow: coreMocks.runOAuthFlow,
  }
})

function renderWithIntl(node: React.ReactNode): ReturnType<typeof render> {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      {node}
    </NextIntlClientProvider>,
  )
}

function adminRow(connectorId: string, name: string, supportedAuthMethods: string[] = ['oauth']) {
  return {
    template: {
      template_id: 'mcptpl_' + connectorId,
      slug: connectorId,
      name,
      provider: name,
      description: '',
      scope: 'global',
      workspace_id: null,
      server_url: 'https://example.com/mcp',
      transport: 'streamable_http',
      supported_auth_methods: supportedAuthMethods,
      default_credential_policy: 'org',
      status: 'active',
    },
    connector: {
      connector_id: connectorId,
      default_credential_policy: 'org',
      discovery_status: 'pending',
      tool_count: 0,
      tools: [],
      tool_citations: {},
      last_error: null,
      auto_enroll_new_workspaces: false,
      org_grant_auth_method: null,
    },
    disabled: false,
    in_use: true,
    needs_attention: true,
    enabled_workspace_count: 0,
    eligible_workspace_count: 1,
    org_grant_status: null,
  } as any
}

function workspaceConnector(connectorId: string, name: string) {
  return {
    template: { provider: name, name },
    install: {
      connector_id: connectorId,
      name,
      auth_method: 'oauth',
      auth_status: 'pending',
      install_scope: 'workspace',
    },
    workspace_state: { enabled: true },
    credential_policy: 'workspace',
    required_grant_scope: 'workspace',
    credential_availability: 'missing',
    credential_source: null,
    credential_availability_by_scope: { org: false, workspace: false, user: false },
    usable: false,
    reason: 'pending_oauth',
  } as any
}

function wsMultiMethodConnector(connectorId: string, name: string) {
  return {
    template: { provider: name, name, supported_auth_methods: ['oauth', 'static'] },
    install: {
      connector_id: connectorId,
      name,
      auth_method: 'none',
      auth_status: 'pending',
      install_scope: 'workspace',
    },
    workspace_state: { enabled: true },
    credential_policy: 'workspace',
    required_grant_scope: 'workspace',
    credential_availability: 'missing',
    credential_source: null,
    credential_availability_by_scope: { org: false, workspace: false, user: false },
    usable: false,
    reason: 'missing_workspace_grant',
  } as any
}

describe('MCP auth band selection changes', () => {
  beforeEach(() => {
    coreMocks.runOAuthFlow.mockReset()
  })

  it('does not carry an admin OAuth start error to the next connector', async () => {
    coreMocks.runOAuthFlow.mockResolvedValueOnce({
      status: 'error',
      reason: 'start_failed:An unexpected error occurred',
    })
    const { rerender } = renderWithIntl(
      <AdminAuthBand
        row={adminRow('mcp_a', 'GitHub')}
        client={{} as any}
        onChanged={async () => undefined}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Connect with GitHub' }))
    await screen.findByText('Could not save credential')
    expect(screen.getByText('An unexpected error occurred')).toBeInTheDocument()

    rerender(
      <NextIntlClientProvider locale="en" messages={en}>
        <AdminAuthBand
          row={adminRow('mcp_b', 'Slack')}
          client={{} as any}
          onChanged={async () => undefined}
        />
      </NextIntlClientProvider>,
    )

    await waitFor(() => {
      expect(screen.queryByText('Could not save credential')).not.toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: 'Connect with Slack' })).toBeInTheDocument()
  })

  it('does not carry a workspace OAuth start error to the next connector', async () => {
    coreMocks.runOAuthFlow.mockResolvedValueOnce({
      status: 'error',
      reason: 'start_failed:An unexpected error occurred',
    })
    const { rerender } = renderWithIntl(
      <WsAuthBand
        connector={workspaceConnector('mcp_a', 'Notion')}
        client={{} as any}
        wsId="ws_1"
        callerRole="admin"
        onChanged={async () => undefined}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Connect with Notion' }))
    await screen.findByText('Could not save credential')
    expect(screen.getByText('An unexpected error occurred')).toBeInTheDocument()

    rerender(
      <NextIntlClientProvider locale="en" messages={en}>
        <WsAuthBand
          connector={workspaceConnector('mcp_b', 'Linear')}
          client={{} as any}
          wsId="ws_1"
          callerRole="admin"
          onChanged={async () => undefined}
        />
      </NextIntlClientProvider>,
    )

    await waitFor(() => {
      expect(screen.queryByText('Could not save credential')).not.toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: 'Connect with Linear' })).toBeInTheDocument()
  })

  it('shows a workspace OAuth start error under React Strict Mode', async () => {
    coreMocks.runOAuthFlow.mockResolvedValueOnce({
      status: 'error',
      reason:
        'start_failed:invalid_redirect_uri: Plaintext HTTP is allowed only for loopback addresses.',
    })
    renderWithIntl(
      <StrictMode>
        <WsAuthBand
          connector={workspaceConnector('mcp_linear', 'Linear')}
          client={{} as any}
          wsId="ws_1"
          callerRole="admin"
          onChanged={async () => undefined}
        />
      </StrictMode>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Connect with Linear' }))

    await screen.findByText('Could not save credential')
    expect(
      screen.getByText(
        'invalid_redirect_uri: Plaintext HTTP is allowed only for loopback addresses.',
      ),
    ).toBeInTheDocument()
  })

  it('shows one Connect button per method when no grant and template supports oauth + static', () => {
    // Durable invariant: when supported=['oauth','static'] and no org grant exists,
    // AdminAuthBand renders two connect actions (one per method) — no auth-method guessing.
    renderWithIntl(
      <AdminAuthBand
        row={adminRow('mcp_multi', 'Acme', ['oauth', 'static'])}
        client={{} as any}
        onChanged={async () => undefined}
      />,
    )

    // OAuth connect button is present (labelled by provider name)
    expect(screen.getByRole('button', { name: 'Connect with Acme' })).toBeInTheDocument()
    // Static connect button is present
    expect(screen.getByTestId('connect-static')).toBeInTheDocument()
  })

  it('ws: shows one Connect button per method when no ws-scope grant and template supports oauth + static', async () => {
    // Durable invariant: when supported=['oauth','static'] and no ws-scope grant exists,
    // WsAuthBand renders WsNoGrantMultiMethod with two connect actions — mirrors the admin band.
    const mockRunOAuthFlow = vi.fn().mockResolvedValue({ status: 'cancelled' })
    coreMocks.runOAuthFlow.mockImplementation(mockRunOAuthFlow)

    const onChanged = vi.fn().mockResolvedValue(undefined)
    renderWithIntl(
      <WsAuthBand
        connector={wsMultiMethodConnector('mcp_ws_multi', 'Acme')}
        client={{} as any}
        wsId="ws_1"
        callerRole="admin"
        onChanged={onChanged}
      />,
    )

    // OAuth connect button is present (labelled by provider name)
    expect(screen.getByTestId('connect-oauth')).toBeInTheDocument()
    // Static connect button is present
    expect(screen.getByTestId('connect-static')).toBeInTheDocument()
  })

  it('ws: org policy + no org grant → does NOT render multi-method Connect buttons (awaiting org admin)', () => {
    // When credential_policy='org' the band state is 'awaiting-others' (not 'needs-action'),
    // so WsNoGrantMultiMethod must NOT render even if supported=['oauth','static'].
    const orgPolicyConnector = {
      template: { provider: 'Acme', name: 'Acme', supported_auth_methods: ['oauth', 'static'] },
      install: {
        connector_id: 'mcp_org_policy',
        name: 'Acme',
        auth_method: 'none',
        auth_status: 'pending',
        install_scope: 'workspace',
      },
      workspace_state: { enabled: true },
      credential_policy: 'org',
      required_grant_scope: 'org',
      credential_availability: 'missing',
      credential_source: null,
      credential_availability_by_scope: { org: false, workspace: false, user: false },
      usable: false,
      reason: 'missing_org_grant',
    } as any

    renderWithIntl(
      <WsAuthBand
        connector={orgPolicyConnector}
        client={{} as any}
        wsId="ws_1"
        callerRole="member"
        onChanged={async () => undefined}
      />,
    )

    // Must not render Connect buttons — caller cannot submit an org grant from the ws page.
    expect(screen.queryByTestId('connect-oauth')).not.toBeInTheDocument()
    expect(screen.queryByTestId('connect-static')).not.toBeInTheDocument()
  })

  it('ws: workspace policy + member caller → does NOT render multi-method Connect buttons (awaiting ws admin)', () => {
    // When credential_policy='workspace' and callerRole='member', the band state is
    // 'awaiting-others' (workspace_admin), so WsNoGrantMultiMethod must NOT render.
    const wsPolicyMemberConnector = {
      template: { provider: 'Acme', name: 'Acme', supported_auth_methods: ['oauth', 'static'] },
      install: {
        connector_id: 'mcp_ws_member',
        name: 'Acme',
        auth_method: 'none',
        auth_status: 'pending',
        install_scope: 'workspace',
      },
      workspace_state: { enabled: true },
      credential_policy: 'workspace',
      required_grant_scope: 'workspace',
      credential_availability: 'missing',
      credential_source: null,
      credential_availability_by_scope: { org: false, workspace: false, user: false },
      usable: false,
      reason: 'missing_workspace_grant',
    } as any

    renderWithIntl(
      <WsAuthBand
        connector={wsPolicyMemberConnector}
        client={{} as any}
        wsId="ws_1"
        callerRole="member"
        onChanged={async () => undefined}
      />,
    )

    // Plain member cannot submit a workspace grant — must not see Connect buttons.
    expect(screen.queryByTestId('connect-oauth')).not.toBeInTheDocument()
    expect(screen.queryByTestId('connect-static')).not.toBeInTheDocument()
  })
})
