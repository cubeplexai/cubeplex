import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import en from '../../messages/en.json'
import { UserMessage } from '@/components/chat/UserMessage'

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <NextIntlClientProvider locale="en" messages={en}>
      {children}
    </NextIntlClientProvider>
  )
}

describe('UserMessage', () => {
  it('renders plain text in user role bubble', () => {
    const { container } = render(<UserMessage content="hello plain world" />, { wrapper })

    expect(screen.getByText('hello plain world')).toBeInTheDocument()
    expect(container.querySelector('[data-role="user"]')).not.toBeNull()
  })

  it('renders markdown formatting like bold, italics, and inline code', () => {
    const { container } = render(
      <UserMessage content="This is **bold** and *italic* with `code`" />,
      { wrapper },
    )

    const strong = container.querySelector('strong')
    expect(strong).not.toBeNull()
    expect(strong?.textContent).toBe('bold')

    const em = container.querySelector('em')
    expect(em).not.toBeNull()
    expect(em?.textContent).toBe('italic')

    const code = container.querySelector('code')
    expect(code).not.toBeNull()
    expect(code?.textContent).toBe('code')
  })

  it('renders inline math formulas with KaTeX', async () => {
    const { container } = render(<UserMessage content="当 $x = \pi$ 时，得到常数 $1$ 和 $0$" />, {
      wrapper,
    })

    await waitFor(() => {
      expect(container.querySelectorAll('.katex').length).toBeGreaterThanOrEqual(1)
    })
  })

  it('preserves currency amounts with single $ literally without KaTeX', () => {
    const { container } = render(
      <UserMessage content="The service costs $100 per month or $5.50 daily" />,
      { wrapper },
    )

    expect(container.querySelector('.katex')).toBeNull()
    expect(container.textContent).toContain('$100')
    expect(container.textContent).toContain('$5.50')
  })
})
