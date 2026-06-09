import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { ThinkingControl } from '@/components/chat/ThinkingControl'
import {
  clearAllPresetSelectionStores,
  getPresetSelectionStore,
} from '@/lib/stores/preset-selection'

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
    render(<ThinkingControl wsId="ws_render" />)
    expect(screen.getByRole('combobox', { name: 'Thinking level' })).toBeInTheDocument()
  })

  it('defaults to thinking="off"', () => {
    render(<ThinkingControl wsId="ws_default" />)
    expect(getPresetSelectionStore('ws_default').getState().thinking).toBe('off')
    // base-ui Select renders the underlying value in the hidden form input.
    const hidden = document.querySelector<HTMLInputElement>('input[aria-hidden="true"]')
    expect(hidden?.value).toBe('off')
  })

  it('reflects the persisted thinking level on render', () => {
    getPresetSelectionStore('ws_high').getState().setThinking('high')
    render(<ThinkingControl wsId="ws_high" />)
    const hidden = document.querySelector<HTMLInputElement>('input[aria-hidden="true"]')
    expect(hidden?.value).toBe('high')
  })
})
