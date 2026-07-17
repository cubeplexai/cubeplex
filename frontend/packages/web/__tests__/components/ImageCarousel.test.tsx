import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import type { Artifact } from '@cubeplex/core'

const mockUrl = (file: string) => `/preview/${file}`
vi.mock('@/components/panel/artifact/previewUtils', () => ({
  buildPreviewUrl: (_a: unknown, file: string) => mockUrl(file as string),
}))

// Stub ImageViewer to surface the url it received.
vi.mock('@/components/shared/previews', () => ({
  ImageViewer: ({ url }: { url: string }) => (
    // eslint-disable-next-line @next/next/no-img-element -- test stub
    <img data-testid="carousel-img" src={url} alt="" />
  ),
}))

import { ImageCarousel } from '@/components/panel/artifact/ImageCarousel'

const artifact = {
  id: 'art-1',
  conversation_id: 'conv-1',
  name: 'Charts',
  artifact_type: 'image' as const,
  path: '/workspace/charts',
  entry_file: null,
  mime_type: null,
  description: null,
  created_at: '2026-06-29T00:00:00Z',
  updated_at: '2026-06-29T00:00:00Z',
  version: 1,
} as unknown as Artifact

describe('ImageCarousel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows first image and counter 1/3, navigates with next/prev', () => {
    render(
      <ImageCarousel
        artifact={artifact}
        imageFiles={['1_a.png', '2_b.png', '3_c.png']}
        version={1}
        workspaceId="ws"
      />,
    )
    expect(screen.getByTestId('carousel-img')).toHaveAttribute('src', '/preview/1_a.png')
    expect(screen.getByText('1 / 3')).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('Next image'))
    expect(screen.getByTestId('carousel-img')).toHaveAttribute('src', '/preview/2_b.png')
    expect(screen.getByText('2 / 3')).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('Previous image'))
    expect(screen.getByTestId('carousel-img')).toHaveAttribute('src', '/preview/1_a.png')
  })

  it('disables prev at start and next at end', () => {
    render(
      <ImageCarousel
        artifact={artifact}
        imageFiles={['1_a.png', '2_b.png']}
        version={1}
        workspaceId="ws"
      />,
    )
    expect(screen.getByLabelText('Previous image')).toBeDisabled()
    fireEvent.click(screen.getByLabelText('Next image'))
    expect(screen.getByLabelText('Next image')).toBeDisabled()
  })

  it('hides nav chrome when single image', () => {
    render(
      <ImageCarousel artifact={artifact} imageFiles={['only.png']} version={1} workspaceId="ws" />,
    )
    expect(screen.getByTestId('carousel-img')).toHaveAttribute('src', '/preview/only.png')
    expect(screen.queryByLabelText('Next image')).not.toBeInTheDocument()
  })
})
