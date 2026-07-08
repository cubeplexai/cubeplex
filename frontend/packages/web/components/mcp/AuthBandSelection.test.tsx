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

vi.mock('@cubebox/core', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@cubebox/core')>()
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

function adminConnector(connectorId: string, name: string) {
  return {
    template: { provider: name, name },
    install: {
      connector_id: connectorId,
      name,
      auth_method: 'oauth',
      auth_status: 'pending',
      default_credential_policy: 'org',
    },
    org_effective: {
      usable: false,
      reason: 'pending_oauth',
      credential_availability: 'missing',
    },
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
    usable: false,
    reason: 'pending_oauth',
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
        connector={adminConnector('mcp_a', 'GitHub')}
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
          connector={adminConnector('mcp_b', 'Slack')}
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
})
