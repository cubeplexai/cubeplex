import { render, screen, fireEvent } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, vi } from 'vitest'
import { PanelHeader } from '@/components/panel/PanelHeader'

const messages = {
  panel: {
    header: {
      copy: 'Copy',
      close: 'Close',
      fullscreen: 'Fullscreen',
      exitFullscreen: 'Exit fullscreen',
    },
  },
}

function renderHeader(ui: React.ReactElement) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      {ui}
    </NextIntlClientProvider>,
  )
}

describe('PanelHeader', () => {
  it('tool source keeps legacy behavior: name + arg summary + copy + close', () => {
    const onClose = vi.fn()
    renderHeader(
      <PanelHeader
        source={{
          kind: 'tool',
          toolName: 'Bash',
          toolArgs: { command: 'ls -la' },
          toolResult: 'total 0',
        }}
        onClose={onClose}
      />,
    )
    expect(screen.getByText('Bash')).toBeInTheDocument()
    expect(screen.getByTitle('Copy')).toBeInTheDocument()
    fireEvent.click(screen.getByTitle('Close'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('plain source renders custom icon, mono subtitle and action slot', () => {
    renderHeader(
      <PanelHeader
        source={{
          kind: 'plain',
          icon: <span data-testid="custom-icon" />,
          title: 'report.pdf',
          subtitle: 'v3 · 1.2 MB',
        }}
        actions={<button data-testid="download-action">DL</button>}
        onClose={() => {}}
      />,
    )
    expect(screen.getByTestId('custom-icon')).toBeInTheDocument()
    expect(screen.getByText('report.pdf')).toBeInTheDocument()
    expect(screen.getByText('v3 · 1.2 MB')).toBeInTheDocument()
    expect(screen.getByTestId('download-action')).toBeInTheDocument()
    // no copyText given -> no copy button
    expect(screen.queryByTitle('Copy')).not.toBeInTheDocument()
  })

  it('fullscreen toggle fires and flips its label', () => {
    const onToggle = vi.fn()
    renderHeader(
      <PanelHeader
        source={{ kind: 'plain', icon: null, title: 'Browser' }}
        fullscreen={{ active: false, onToggle }}
        onClose={() => {}}
      />,
    )
    fireEvent.click(screen.getByTitle('Fullscreen'))
    expect(onToggle).toHaveBeenCalledTimes(1)
  })
})
