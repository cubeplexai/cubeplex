import { beforeEach, describe, expect, it } from 'vitest'
import { useArtifactStore, usePanelStore } from '@cubeplex/core'

import { resetWorkspaceScopedClientState } from './resetWorkspaceScopedState'

describe('resetWorkspaceScopedClientState', () => {
  beforeEach(() => {
    usePanelStore.setState({ view: { type: 'closed' } })
    useArtifactStore.setState({ artifacts: {} })
  })

  it('closes an open panel so a workspace switch cannot leave an empty rail', () => {
    usePanelStore.getState().openArtifact('conv-1', 'art-1')
    expect(usePanelStore.getState().view.type).toBe('artifact')

    resetWorkspaceScopedClientState()

    expect(usePanelStore.getState().view).toEqual({ type: 'closed' })
  })

  it('closes a sandbox panel as well as artifact/tool views', () => {
    usePanelStore.getState().openSandbox()
    resetWorkspaceScopedClientState()
    expect(usePanelStore.getState().view).toEqual({ type: 'closed' })
  })
})
