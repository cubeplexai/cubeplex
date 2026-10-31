import { fireEvent, render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, vi } from 'vitest'

import en from '../../messages/en.json'

import { EffortSlider } from '@/components/chat/EffortSlider'

function renderSlider(value: 'default' | 'off' | 'minimal' = 'default'): {
  onChange: ReturnType<typeof vi.fn>
} {
  const onChange = vi.fn()
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <EffortSlider levels={['default', 'off', 'minimal']} value={value} onChange={onChange} />
    </NextIntlClientProvider>,
  )
  return { onChange }
}

describe('EffortSlider', () => {
  it('shows the short default label and steps from default to off', () => {
    const { onChange } = renderSlider()

    const slider = screen.getByRole('slider', { name: en.chat.effortLabel })
    expect(slider).toHaveAttribute('aria-valuetext', 'Default')
    expect(screen.getByText('Default')).toBeInTheDocument()

    fireEvent.keyDown(slider, { key: 'ArrowRight' })
    expect(onChange).toHaveBeenCalledWith('off')
  })
})
