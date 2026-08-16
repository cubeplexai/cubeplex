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
      retry: 'Retry',
      statusStopping: 'Stopping…',
      statusStopped: 'Stopped',
      statusReconnecting: 'Reconnecting…',
      statusDisconnected: 'Connection lost',
      statusFailed: 'Reply failed',
      statusIncomplete: 'Incomplete',
      ariaStopping: 'This run is stopping',
      ariaStopped: 'This run was stopped',
      ariaReconnecting: 'Reconnecting to this run',
      ariaDisconnected: 'Connection to this run was lost',
      ariaFailed: 'This run failed',
      ariaIncomplete: 'Previous response was incomplete',
      detailStopping: 'Stopping this response.',
      detailStopped: 'You stopped this response.',
      detailReconnecting: 'Connection dropped. The run is still going.',
      detailDisconnected: 'Connection dropped. The run is still going.',
      detailFailed: 'The model or a tool failed during this turn.',
      detailIncomplete: 'Previous response was incomplete (service exit). Please retry.',
    },
  },
}

function renderChip(runId: string | null | undefined, status?: 'completed' | 'stopped' | 'failed') {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <RunInfoChip runId={runId} status={status} />
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

  it('shows a stopped label without treating it as a failure alert', () => {
    renderChip('run-stop', 'stopped')
    expect(screen.getByRole('button', { name: 'This run was stopped' })).toHaveTextContent(
      'Stopped',
    )
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
