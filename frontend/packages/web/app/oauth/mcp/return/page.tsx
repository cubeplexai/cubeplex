'use client'

/**
 * OAuth return page (popup side).
 * Spec: docs/superpowers/specs/2026-05-16-mcp-install-auth-handoff-spec.md §5.6.
 *
 * - Posts a typed message on BroadcastChannel('cubebox-mcp-oauth'), then
 *   closes itself after a 250ms grace period.
 * - If `state` is missing entirely (the genuinely-unrecoverable path),
 *   renders a static fallback and DOES NOT broadcast or auto-close.
 */

import React, { useEffect } from 'react'
import { useSearchParams } from 'next/navigation'

const CHANNEL_NAME = 'cubebox-mcp-oauth'

export default function OAuthReturnPage(): React.ReactElement {
  const params = useSearchParams()
  const status = params.get('status') ?? 'error'
  const state = params.get('state')
  const connectorId = params.get('connector_id') ?? ''
  const reason = params.get('reason') ?? undefined

  useEffect(() => {
    if (state === null || state === '') {
      // Hostile or stray navigation. Spec §5.6: do not broadcast,
      // do not auto-close. Show fallback.
      return
    }
    const channel = new BroadcastChannel(CHANNEL_NAME)
    channel.postMessage({
      kind: 'mcp.oauth.return',
      status,
      state,
      connector_id: connectorId,
      reason,
    })
    const close = setTimeout(() => {
      channel.close()
      try {
        window.close()
      } catch {
        /* fallback below */
      }
    }, 250)
    return () => {
      clearTimeout(close)
      channel.close()
    }
  }, [status, state, connectorId, reason])

  return (
    <div
      style={{
        display: 'flex',
        height: '100vh',
        alignItems: 'center',
        justifyContent: 'center',
        fontFamily: 'system-ui, sans-serif',
        padding: '2rem',
        textAlign: 'center',
      }}
    >
      <div>
        <h1 style={{ fontSize: '1.25rem', marginBottom: '0.5rem' }}>
          {state ? 'You can close this window' : 'Sign-in failed'}
        </h1>
        <p style={{ color: '#666', fontSize: '0.875rem' }}>
          {state
            ? 'Authorization complete. Your other tab will pick up the result.'
            : 'Please close this window and retry from the connector page.'}
        </p>
      </div>
    </div>
  )
}
