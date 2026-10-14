import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { PresentedFileCard } from '@/components/chat/PresentedFileCard'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

vi.mock('@/hooks/useWorkspaceContext', () => ({
  useWorkspaceContext: () => ({ workspaceId: 'ws-test' }),
}))

const qrFile = {
  id: 'pfile-abc',
  conversation_id: 'conv-1',
  filename: 'qr.png',
  mime_type: 'image/png',
  size_bytes: 100,
  kind: 'image',
  caption: 'Login QR',
}

describe('PresentedFileCard', () => {
  it('loads the original image directly and links to the same file', () => {
    render(<PresentedFileCard file={qrFile} />)
    const img = screen.getByRole('img', { name: 'Login QR' })
    expect(img).toHaveAttribute(
      'src',
      '/api/v1/ws/ws-test/conversations/conv-1/presented-files/pfile-abc/content',
    )
    expect(img).toHaveAttribute('loading', 'lazy')
    const link = img.closest('a')
    expect(link).toHaveAttribute(
      'href',
      '/api/v1/ws/ws-test/conversations/conv-1/presented-files/pfile-abc/content',
    )
    expect(link).toHaveClass('w-full')
  })

  it('keeps the original downloadable when the image fails to load', () => {
    render(<PresentedFileCard file={qrFile} />)
    fireEvent.error(screen.getByRole('img', { name: 'Login QR' }))

    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    const link = screen.getByRole('link', { name: /qr\.png/ })
    expect(link).toHaveAttribute(
      'href',
      '/api/v1/ws/ws-test/conversations/conv-1/presented-files/pfile-abc/content',
    )
    expect(link).toHaveAttribute('download', 'qr.png')
  })

  it('uses decoded image dimensions when EXIF rotation differs from stored dimensions', () => {
    render(<PresentedFileCard file={{ ...qrFile, width: 640, height: 320 }} />)
    const img = screen.getByRole('img', { name: 'Login QR' })
    Object.defineProperties(img, {
      naturalWidth: { value: 320 },
      naturalHeight: { value: 640 },
    })

    fireEvent.load(img)

    expect(img.closest('a')).toHaveStyle({ width: '320px' })
    expect(img.parentElement).toHaveStyle({ aspectRatio: '320 / 640' })
    expect(img).toHaveAttribute('width', '320')
    expect(img).toHaveAttribute('height', '640')
  })

  it('sizes small images to their native pixel width instead of stretching', () => {
    render(<PresentedFileCard file={{ ...qrFile, width: 256, height: 256 }} />)
    const img = screen.getByRole('img', { name: 'Login QR' })
    expect(img).toHaveAttribute('width', '256')
    expect(img).toHaveAttribute('height', '256')
    const link = img.closest('a')
    expect(link).toHaveStyle({ width: '256px' })
    expect(link).not.toHaveClass('w-full')
    expect(img.parentElement).toHaveStyle({ aspectRatio: '256 / 256' })
  })

  it('caps oversized images at 480px while preserving aspect ratio', () => {
    render(
      <PresentedFileCard file={{ ...qrFile, caption: 'Screenshot', width: 1600, height: 900 }} />,
    )
    const img = screen.getByRole('img', { name: 'Screenshot' })
    expect(img.closest('a')).toHaveStyle({ width: '480px' })
    expect(img.parentElement).toHaveStyle({ aspectRatio: '1600 / 900' })
  })

  it('shows loading placeholder when file is null', () => {
    render(<PresentedFileCard file={null} />)
    expect(screen.getByText('presentFileLoading')).toBeInTheDocument()
  })
})
