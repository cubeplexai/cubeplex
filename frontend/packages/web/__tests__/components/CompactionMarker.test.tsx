import { render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it } from 'vitest'
import en from '../../messages/en.json'
import { CompactionMarker, showCompactionPendingLine } from '../../components/chat/CompactionMarker'

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <NextIntlClientProvider locale="en" messages={en}>
      {children}
    </NextIntlClientProvider>
  )
}

const savedMarker = {
  role: 'user',
  metadata: { synthetic: true, synthetic_source: 'compaction', kind: 'compaction' },
}

describe('CompactionMarker', () => {
  it('shows the finished label without a busy spinner', () => {
    render(<CompactionMarker source="manual" />, { wrapper })
    const marker = screen.getByTestId('compaction-marker')
    expect(marker).toHaveTextContent('Context compacted')
    expect(marker).not.toHaveAttribute('aria-busy', 'true')
    expect(marker.querySelector('.animate-spin')).toBeNull()
  })

  it('shows the waiting label and a spinner while compact is in flight', () => {
    render(<CompactionMarker pending />, { wrapper })
    const marker = screen.getByTestId('compaction-marker')
    expect(marker).toHaveTextContent('Compacting context…')
    expect(marker).toHaveAttribute('aria-busy', 'true')
    expect(marker).toHaveAttribute('aria-label', 'Compacting context…')
    expect(marker.querySelector('.animate-spin')).not.toBeNull()
  })
})

describe('showCompactionPendingLine', () => {
  it('shows the waiting line only while compacting and the tail is not already a marker', () => {
    expect(showCompactionPendingLine(false, null)).toBe(false)
    expect(showCompactionPendingLine(true, null)).toBe(true)
    expect(showCompactionPendingLine(true, { role: 'assistant', metadata: null })).toBe(true)
    expect(showCompactionPendingLine(true, savedMarker)).toBe(false)
  })
})
