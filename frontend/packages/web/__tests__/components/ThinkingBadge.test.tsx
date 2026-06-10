import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { ThinkingBadge } from '@/components/chat/ThinkingBadge'
import {
  clearAllPresetSelectionStores,
  getPresetSelectionStore,
} from '@/lib/stores/preset-selection'
import type { ThinkingLevel } from '@/lib/types/presets'

beforeEach(() => {
  localStorage.clear()
  clearAllPresetSelectionStores()
})

afterEach(() => {
  localStorage.clear()
  clearAllPresetSelectionStores()
})

describe('ThinkingBadge', () => {
  it('renders nothing when thinking === "off"', () => {
    const { container } = render(<ThinkingBadge wsId="ws_off" />)
    expect(container).toBeEmptyDOMElement()
  })

  it.each<ThinkingLevel>(['low', 'medium', 'high', 'xhigh'])(
    'renders the badge when thinking === %s',
    (level) => {
      getPresetSelectionStore(`ws_${level}`).getState().setThinking(level)
      render(<ThinkingBadge wsId={`ws_${level}`} />)
      const node = screen.getByRole('status', { name: `Thinking level ${level}` })
      expect(node).toHaveTextContent(`thinking: ${level}`)
    },
  )
})
