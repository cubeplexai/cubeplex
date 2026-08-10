import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { RunInfoChip } from '@/components/chat/RunInfoChip'

const messages = {
  chat: {
    runInfo: {
      label: 'Info',
      ariaLabel: 'Run info',
      runIdLabel: 'Run ID',
      copy: 'Copy run ID',
      copied: 'Copied',
    },
  },
}

function renderChip(runId: string | null | undefined) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <RunInfoChip runId={runId} />
    </NextIntlClientProvider>,
  )
}

describe('RunInfoChip', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('renders nothing when runId is missing', () => {
    const { container } = renderChip(null)
    expect(container).toBeEmptyDOMElement()
  })

  it('opens a popover that shows the run_id', async () => {
    renderChip('run-abc-123')
    fireEvent.click(screen.getByRole('button', { name: 'Run info' }))
    expect(await screen.findByText('run-abc-123')).toBeInTheDocument()
    expect(screen.getByText('Run ID')).toBeInTheDocument()
  })

  it('copies the run_id when the copy button is clicked', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })

    renderChip('run-xyz')
    fireEvent.click(screen.getByRole('button', { name: 'Run info' }))
    const copyBtn = await screen.findByRole('button', { name: 'Copy run ID' })
    fireEvent.click(copyBtn)

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith('run-xyz')
    })
    expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument()
  })
})
