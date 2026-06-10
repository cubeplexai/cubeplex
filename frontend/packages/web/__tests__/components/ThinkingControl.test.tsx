import { render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import en from '../../messages/en.json'

import { ThinkingControl } from '@/components/chat/ThinkingControl'
import {
  clearAllPresetSelectionStores,
  getPresetSelectionStore,
} from '@/lib/stores/preset-selection'

function renderWithIntl(ui: React.ReactElement): ReturnType<typeof render> {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      {ui}
    </NextIntlClientProvider>,
  )
}

beforeEach(() => {
  localStorage.clear()
  clearAllPresetSelectionStores()
})

afterEach(() => {
  localStorage.clear()
  clearAllPresetSelectionStores()
})

describe('ThinkingControl', () => {
  it('renders a combobox trigger labeled "Thinking level"', () => {
    renderWithIntl(<ThinkingControl wsId="ws_render" />)
    expect(screen.getByRole('combobox', { name: 'Thinking level' })).toBeInTheDocument()
  })

  it('defaults to thinking="off"', () => {
    renderWithIntl(<ThinkingControl wsId="ws_default" />)
    expect(getPresetSelectionStore('ws_default').getState().thinking).toBe('off')
    // base-ui Select renders the underlying value in the hidden form input.
    const hidden = document.querySelector<HTMLInputElement>('input[aria-hidden="true"]')
    expect(hidden?.value).toBe('off')
  })

  it('reflects the persisted thinking level on render', () => {
    getPresetSelectionStore('ws_high').getState().setThinking('high')
    renderWithIntl(<ThinkingControl wsId="ws_high" />)
    const hidden = document.querySelector<HTMLInputElement>('input[aria-hidden="true"]')
    expect(hidden?.value).toBe('high')
  })
})
