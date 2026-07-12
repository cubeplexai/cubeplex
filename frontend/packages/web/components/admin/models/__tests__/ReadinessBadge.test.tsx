import { render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it } from 'vitest'
import type { Readiness } from '@cubeplex/core'
import en from '../../../../messages/en.json'
import { ReadinessBadge } from '../ReadinessBadge'

function renderBadge(readiness: Readiness): ReturnType<typeof render> {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      <ReadinessBadge readiness={readiness} />
    </NextIntlClientProvider>,
  )
}

describe('ReadinessBadge', () => {
  it('renders a green dot and accessible label for ready', () => {
    const { container } = renderBadge('ready')
    expect(screen.getByTitle('Ready')).toBeInTheDocument()
    expect(screen.getByText('Ready')).toBeInTheDocument()
    expect(container.querySelector('.bg-success-solid')).toBeInTheDocument()
  })

  it('renders a red dot for model_error', () => {
    const { container } = renderBadge('model_error')
    expect(screen.getByTitle('Model test failed')).toBeInTheDocument()
    expect(container.querySelector('.bg-danger-solid')).toBeInTheDocument()
  })

  it('renders an amber dot for degraded', () => {
    const { container } = renderBadge('degraded')
    expect(screen.getByTitle('Degraded')).toBeInTheDocument()
    expect(container.querySelector('.bg-warning-solid')).toBeInTheDocument()
  })
})
