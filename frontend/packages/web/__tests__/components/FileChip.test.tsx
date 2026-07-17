import { render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it } from 'vitest'
import type { UploadingFile } from '@cubeplex/core'
import en from '../../messages/en.json'
import { FileChip } from '../../components/chat/FileChip'

function renderWithIntl(ui: React.ReactElement): ReturnType<typeof render> {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      {ui}
    </NextIntlClientProvider>,
  )
}

describe('FileChip', () => {
  it('shows a localized invalid MIME upload error', () => {
    const item: UploadingFile = {
      tempId: 'tmp-1',
      filename: 'archive.rar',
      size: 1024,
      progress: 0,
      status: 'error',
      error: 'File type is not allowed.',
      errorCode: 'INVALID_MIME_TYPE',
    }

    renderWithIntl(<FileChip item={item} onCancel={() => undefined} />)

    expect(screen.getByText('File type is not allowed.')).toBeInTheDocument()
    expect(screen.queryByText('Upload failed')).not.toBeInTheDocument()
  })
})
