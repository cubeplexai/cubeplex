import { render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, vi } from 'vitest'
import en from '../../messages/en.json'
import { EEGate } from '../../components/admin/EEGate'

/**
 * The OSS branch of the edition gate.
 *
 * CI runs the whole Playwright suite against a licensed backend so the
 * EE-only admin specs can drive their pages, which means no browser test
 * exercises the unlicensed path. These cover it: an OSS deployment must not
 * render EE page content, and a licensed one must not render the upsell.
 */

const edition = vi.hoisted(() => ({ value: 'oss' as 'oss' | 'ee', loading: false }))

vi.mock('@cubeplex/core/hooks/useEdition', () => ({
  useEdition: () => ({
    edition: edition.value,
    features: [],
    hasFeature: () => false,
    loading: edition.loading,
  }),
}))

function renderGate() {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      <EEGate>
        <p>enterprise page body</p>
      </EEGate>
    </NextIntlClientProvider>,
  )
}

describe('EEGate', () => {
  it('withholds EE page content on an OSS deployment', () => {
    edition.value = 'oss'
    edition.loading = false
    renderGate()
    expect(screen.queryByText('enterprise page body')).toBeNull()
    expect(screen.getByText(en.adminLayout.eeOnlyTitle)).toBeTruthy()
  })

  it('renders the page on a licensed deployment', () => {
    edition.value = 'ee'
    edition.loading = false
    renderGate()
    expect(screen.getByText('enterprise page body')).toBeTruthy()
    expect(screen.queryByText(en.adminLayout.eeOnlyTitle)).toBeNull()
  })

  it('shows neither state until the edition is known', () => {
    // Defaulting to 'oss' while loading would flash the upsell at licensed users.
    edition.value = 'oss'
    edition.loading = true
    renderGate()
    expect(screen.queryByText('enterprise page body')).toBeNull()
    expect(screen.queryByText(en.adminLayout.eeOnlyTitle)).toBeNull()
  })
})
