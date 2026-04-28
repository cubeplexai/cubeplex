import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MessageAttachments, type MessageAttachmentDto } from '@/components/chat/MessageAttachments'

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
    const { container } = render(<MessageAttachments attachments={[]} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders image as a button with thumbnail', () => {
    render(<MessageAttachments attachments={[image]} />)
    expect(screen.getByRole('button')).toBeInTheDocument()
    expect(screen.getByAltText('chart.png')).toBeInTheDocument()
  })

  it('renders document as a download link', () => {
    render(<MessageAttachments attachments={[doc]} />)
    const link = screen.getByRole('link')
    expect(link).toHaveAttribute('href', '/d')
    expect(screen.getByText('spec.pdf')).toBeInTheDocument()
  })
})
