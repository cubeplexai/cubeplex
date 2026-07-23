import { render, screen, fireEvent, within, act } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Artifact } from '@cubeplex/core'
import { useArtifactStore, usePanelStore } from '@cubeplex/core'
import { WorkspaceContext } from '@/hooks/useWorkspaceContext'

vi.mock('@cubeplex/core', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@cubeplex/core')>()
  return {
    ...actual,
    createApiClient: () => ({ setWorkspaceId: vi.fn() }),
  }
})

vi.mock('@/components/panel/artifact/FallbackPreview', () => ({
  FallbackPreview: () => <div data-testid="preview-host">preview-body</div>,
}))

vi.mock('next/dynamic', () => ({
  default: () => () => null,
}))

import { ArtifactPanel } from '@/components/panel/artifact/ArtifactPanel'

const messages = {
  panel: {
    header: {
      copy: 'Copy',
      close: 'Close',
      expand: 'Expand preview',
      exitExpand: 'Exit expand',
    },
    artifactPanel: {
      download: 'Download',
      close: 'Close',
      expand: 'Expand preview',
      exitExpand: 'Exit expand',
      expandedPlaceholder: 'Expanded in preview',
      unknownType: 'Unknown type',
      justNow: 'just now',
      minutesAgo: '{n}m ago',
      hoursAgo: '{n}h ago',
      daysAgo: '{n}d ago',
    },
  },
}

const artifact: Artifact = {
  id: 'art-1',
  conversation_id: 'conv-1',
  name: 'report.bin',
  artifact_type: 'file',
  path: '/workspace/report.bin',
  entry_file: null,
  mime_type: 'application/octet-stream',
  description: null,
  created_at: '2026-07-22T00:00:00Z',
  updated_at: '2026-07-22T00:00:00Z',
  version: 1,
}

const otherArtifact: Artifact = {
  ...artifact,
  id: 'art-2',
  name: 'other.bin',
  path: '/workspace/other.bin',
}

function seedArtifact(a: Artifact = artifact): void {
  useArtifactStore.setState({
    artifacts: { [a.conversation_id]: { [a.id]: a } },
    versions: {},
    selectedVersion: {},
    loading: {},
    deletedIds: {},
  })
  usePanelStore.getState().openArtifact(a.conversation_id, a.id)
}

function renderPanel() {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <WorkspaceContext.Provider value={{ workspaceId: 'ws-1' }}>
        <ArtifactPanel />
      </WorkspaceContext.Provider>
    </NextIntlClientProvider>,
  )
}

describe('ArtifactPanel expand theater', () => {
  beforeEach(() => {
    usePanelStore.setState({ view: { type: 'closed' } })
    useArtifactStore.setState({
      artifacts: {},
      versions: {},
      selectedVersion: {},
      loading: {},
      deletedIds: {},
    })
    seedArtifact()
  })

  it('opens in-app expand and unmounts rail preview (single host)', () => {
    renderPanel()
    expect(screen.getByTestId('artifact-rail-preview')).toBeInTheDocument()
    expect(screen.getAllByTestId('preview-host')).toHaveLength(1)

    fireEvent.click(screen.getByTitle('Expand preview'))

    expect(screen.getByTestId('artifact-rail-placeholder')).toBeInTheDocument()
    expect(screen.queryByTestId('artifact-rail-preview')).not.toBeInTheDocument()
    expect(screen.getByTestId('artifact-expand-preview')).toBeInTheDocument()
    // Only the theater hosts the preview while expanded.
    expect(screen.getAllByTestId('preview-host')).toHaveLength(1)
    expect(
      within(screen.getByTestId('artifact-expand-preview')).getByTestId('preview-host'),
    ).toBeInTheDocument()
  })

  it.each([
    {
      name: 'minimize',
      exit: () => {
        // Theater header minimize (active expand)
        fireEvent.click(screen.getAllByTitle('Exit expand')[0]!)
      },
    },
    {
      name: 'theater X',
      exit: () => {
        // Theater has Close; rail also has Close — use the expand dialog's close.
        const dialog = screen.getByRole('dialog')
        fireEvent.click(within(dialog).getByTitle('Close'))
      },
    },
  ])('exit via $name keeps panelStore selection', ({ exit }) => {
    renderPanel()
    fireEvent.click(screen.getByTitle('Expand preview'))
    expect(screen.getByTestId('artifact-expand-preview')).toBeInTheDocument()

    exit()

    expect(screen.queryByTestId('artifact-expand-preview')).not.toBeInTheDocument()
    expect(screen.getByTestId('artifact-rail-preview')).toBeInTheDocument()
    expect(usePanelStore.getState().view).toEqual({
      type: 'artifact',
      conversationId: 'conv-1',
      artifactId: 'art-1',
    })
  })

  it('Esc closes theater and keeps selection', () => {
    renderPanel()
    fireEvent.click(screen.getByTitle('Expand preview'))
    const popup = document.querySelector('[data-slot="dialog-content"]')
    expect(popup).toBeTruthy()

    fireEvent.keyDown(popup!, { key: 'Escape' })

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByTestId('artifact-rail-preview')).toBeInTheDocument()
    expect(usePanelStore.getState().view).toEqual({
      type: 'artifact',
      conversationId: 'conv-1',
      artifactId: 'art-1',
    })
  })

  it('panel rail Close closes the whole panel', () => {
    renderPanel()
    fireEvent.click(screen.getByTitle('Expand preview'))

    // Rail header Close is outside the dialog
    const rail = screen.getByTestId('artifact-rail-placeholder').parentElement!
    const railHeaderClose = within(rail.parentElement!).getAllByTitle('Close')[0]!
    fireEvent.click(railHeaderClose)

    expect(usePanelStore.getState().view).toEqual({ type: 'closed' })
    expect(screen.queryByTestId('artifact-expand-preview')).not.toBeInTheDocument()
  })

  it('navigate-away to another artifact closes theater without dual host', () => {
    renderPanel()
    fireEvent.click(screen.getByTitle('Expand preview'))
    expect(screen.getByTestId('artifact-expand-preview')).toBeInTheDocument()

    act(() => {
      useArtifactStore.setState({
        artifacts: {
          'conv-1': { 'art-1': artifact, 'art-2': otherArtifact },
        },
      })
      usePanelStore.getState().openArtifact('conv-1', 'art-2')
    })

    // Identity-keyed expand: theater closes when artifact id changes.
    expect(screen.queryByTestId('artifact-expand-preview')).not.toBeInTheDocument()
    expect(screen.getByTestId('artifact-rail-preview')).toBeInTheDocument()
    expect(screen.getAllByTestId('preview-host')).toHaveLength(1)
    expect(screen.getByText('other.bin')).toBeInTheDocument()
  })

  it('navigate-away to non-artifact panel unmounts expand', () => {
    renderPanel()
    fireEvent.click(screen.getByTitle('Expand preview'))
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    act(() => {
      usePanelStore.getState().openSandbox()
    })

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(usePanelStore.getState().view.type).toBe('sandbox')
  })
})
