import { render, screen, fireEvent } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import type { Artifact } from '@cubeplex/core'
import en from '../../messages/en.json'
import { ImageArtifactCard } from '../../components/chat/ImageArtifactCard'

const openArtifact = vi.fn()

vi.mock('@cubeplex/core', () => ({
  usePanelStore: (selector: (state: { openArtifact: typeof openArtifact }) => unknown) =>
    selector({ openArtifact }),
}))

vi.mock('@/hooks/useWorkspaceContext', () => ({
  useWorkspaceContext: () => ({ workspaceId: 'ws-1' }),
}))

function renderWithIntl(ui: React.ReactElement): ReturnType<typeof render> {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      {ui}
    </NextIntlClientProvider>,
  )
}

const singleImageArtifact: Artifact = {
  id: 'art-1',
  conversation_id: 'conv-1',
  name: 'Chart',
  artifact_type: 'image',
  path: '/workspace/chart.png',
  entry_file: 'chart.png',
  mime_type: 'image/png',
  description: null,
  created_at: '2026-06-29T00:00:00Z',
  updated_at: '2026-06-29T00:00:00Z',
  version: 1,
}

const multiImageArtifact: Artifact = {
  ...singleImageArtifact,
  id: 'art-2',
  path: '/workspace/charts',
  entry_file: null,
}

describe('ImageArtifactCard', () => {
  beforeEach(() => {
    openArtifact.mockClear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders a single-image artifact directly, without a files fetch', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    renderWithIntl(<ImageArtifactCard caption="a chart" artifact={singleImageArtifact} />)

    const img = screen.getByAltText('a chart')
    expect(img).toHaveAttribute(
      'src',
      '/api/v1/ws/ws-1/conversations/conv-1/artifacts/art-1/preview/v1/chart.png',
    )
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('resolves and renders the first image of a multi-image artifact, with a count badge', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ version: 1, files: ['1_a.png', '2_b.png', '3_c.png'] }),
    })
    vi.stubGlobal('fetch', fetchMock)

    renderWithIntl(<ImageArtifactCard caption="a gallery" artifact={multiImageArtifact} />)

    const img = await screen.findByAltText('a gallery')
    expect(img).toHaveAttribute(
      'src',
      '/api/v1/ws/ws-1/conversations/conv-1/artifacts/art-2/preview/v1/1_a.png',
    )
    expect(await screen.findByText('×3')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/ws/ws-1/conversations/conv-1/artifacts/art-2/files?filter=image',
    )

    fireEvent.click(img)
    expect(openArtifact).toHaveBeenCalledWith('conv-1', 'art-2')
  })

  it('shows the generating placeholder while artifact is null', () => {
    renderWithIntl(<ImageArtifactCard caption="" artifact={null} />)
    expect(screen.getByText('Generating image…')).toBeInTheDocument()
  })
})
