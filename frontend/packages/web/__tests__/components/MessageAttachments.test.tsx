import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MessageAttachments, type MessageAttachmentDto } from '@/components/chat/MessageAttachments'

vi.mock('@cubeplex/core', () => ({
  createApiClient: () => ({
    workspaceId: null,
    setWorkspaceId: vi.fn(),
    resolvePath: (path: string) => path,
  }),
  usePanelStore: (selector: (state: { openAttachment: () => void }) => unknown) =>
    selector({ openAttachment: vi.fn() }),
}))

vi.mock('@/hooks/useWorkspaceContext', () => ({
  useWorkspaceContext: () => ({ workspaceId: 'test-ws' }),
}))

const image: MessageAttachmentDto = {
  id: 'i1',
  filename: 'chart.png',
  kind: 'image',
  size_bytes: 1024,
  width: 100,
  height: 100,
  thumbnail_url: '/thumb',
  download_url: '/download',
}
const doc: MessageAttachmentDto = {
  id: 'd1',
  filename: 'spec.pdf',
  kind: 'document',
  size_bytes: 2048,
  download_url: '/d',
}

describe('MessageAttachments', () => {
  it('renders nothing when empty', () => {
    const { container } = render(<MessageAttachments attachments={[]} conversationId="test-conv" />)
    expect(container.firstChild).toBeNull()
  })

  it('renders image as a button with thumbnail', () => {
    render(<MessageAttachments attachments={[image]} conversationId="test-conv" />)
    expect(screen.getByRole('button')).toBeInTheDocument()
    expect(screen.getByAltText('chart.png')).toBeInTheDocument()
  })

  it('renders document as a download link', () => {
    render(<MessageAttachments attachments={[doc]} conversationId="test-conv" />)
    const link = screen.getByRole('link')
    expect(link).toHaveAttribute('href', '/d')
    expect(screen.getByText('spec.pdf')).toBeInTheDocument()
  })
})
