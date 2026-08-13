import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { PresentedFileCard } from '@/components/chat/PresentedFileCard'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

vi.mock('@/hooks/useWorkspaceContext', () => ({
  useWorkspaceContext: () => ({ workspaceId: 'ws-test' }),
}))

describe('PresentedFileCard', () => {
  it('renders image with content URL', () => {
    render(
      <PresentedFileCard
        file={{
          id: 'pfile-abc',
          conversation_id: 'conv-1',
          filename: 'qr.png',
          mime_type: 'image/png',
          size_bytes: 100,
          kind: 'image',
          caption: 'Login QR',
        }}
      />,
    )
    const img = screen.getByRole('img', { name: 'Login QR' })
    expect(img).toHaveAttribute(
      'src',
      '/api/v1/ws/ws-test/conversations/conv-1/presented-files/pfile-abc/content',
    )
  })

  it('shows loading placeholder when file is null', () => {
    render(<PresentedFileCard file={null} />)
    expect(screen.getByText('presentFileLoading')).toBeInTheDocument()
  })
})
