import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import {
  MarkdownWithCitations,
  escapeCurrencyDollars,
  containsMath,
} from '../MarkdownWithCitations'

describe('MarkdownWithCitations memoization', () => {
  it('is exported as a React.memo component', () => {
    // React.memo returns an object with $$typeof === Symbol.for('react.memo').
    // Checking the wrapper marker is the most reliable way to assert the
    // memoization barrier is in place — DOM-identity checks would pass even
    // without memo because React reconciliation reuses same-type nodes.
    const marker = (MarkdownWithCitations as unknown as { $$typeof?: symbol }).$$typeof
    expect(marker).toBe(Symbol.for('react.memo'))
  })

  it('still renders markdown correctly', () => {
    render(
      <MarkdownWithCitations conversationId="conv-test">hello **world**</MarkdownWithCitations>,
    )
    expect(screen.getByText('world')).toBeInTheDocument()
    expect(screen.getByText('world').tagName).toBe('STRONG')
  })

  it('updates output when children text changes', () => {
    const { rerender } = render(
      <MarkdownWithCitations conversationId="conv-test">alpha</MarkdownWithCitations>,
    )
    expect(screen.getByText('alpha')).toBeInTheDocument()
    rerender(<MarkdownWithCitations conversationId="conv-test">beta</MarkdownWithCitations>)
    expect(screen.getByText('beta')).toBeInTheDocument()
  })
})

describe('MarkdownWithCitations math rendering', () => {
  it('renders currency amounts with single $ literally (no KaTeX)', () => {
    const text = 'Monthly revenue increased from $271,677.37 in January to $272,748.58 in February'
    const { container } = render(
      <MarkdownWithCitations conversationId="conv-test">{text}</MarkdownWithCitations>,
    )
    // Single-$ spans must NOT be parsed as math (no katex markup injected).
    expect(container.querySelector('.katex')).toBeNull()
    // The dollar amounts must survive as literal text.
    expect(container.textContent).toContain('$271,677.37')
    expect(container.textContent).toContain('$272,748.58')
  })

  it('still renders $$…$$ display math as KaTeX', async () => {
    const { container } = render(
      <MarkdownWithCitations conversationId="conv-test">
        {'E = mc^2 in $$E=mc^2$$ form'}
      </MarkdownWithCitations>,
    )
    await waitFor(() => expect(container.querySelector('.katex')).not.toBeNull())
  })

  it('renders single $ inline math as KaTeX', async () => {
    const text = '当 $x = \\pi$ 时，就会得到欧拉恒等式'
    const { container } = render(
      <MarkdownWithCitations conversationId="conv-test">{text}</MarkdownWithCitations>,
    )
    await waitFor(() => expect(container.querySelector('.katex')).not.toBeNull())
    expect(container.textContent).toContain('当')
    expect(container.textContent).toContain('时，就会得到欧拉恒等式')
  })

  it('renders single-digit and constant inline formulas ($1$, $0$) as KaTeX', async () => {
    const text =
      '它把数学中最关键的五个常数（$e$、$i$、$\\pi$、$1$、$0$）和基本运算完美地联系在了一起～'
    const { container } = render(
      <MarkdownWithCitations conversationId="conv-test">{text}</MarkdownWithCitations>,
    )
    await waitFor(() => expect(container.querySelectorAll('.katex')).toHaveLength(5))
  })

  it('renders formulas starting with digits ($1 + 1 = 2$, $2\\pi$) as KaTeX', async () => {
    const text = 'Formula $1 + 1 = 2$ and $2\\pi$ in math'
    const { container } = render(
      <MarkdownWithCitations conversationId="conv-test">{text}</MarkdownWithCitations>,
    )
    await waitFor(() => expect(container.querySelectorAll('.katex')).toHaveLength(2))
  })

  it('renders both inline math and currency correctly in the same text', async () => {
    const text = 'Formula $x = \\pi$ costs $50 to license, previously $40.'
    const { container } = render(
      <MarkdownWithCitations conversationId="conv-test">{text}</MarkdownWithCitations>,
    )
    await waitFor(() => expect(container.querySelector('.katex')).not.toBeNull())
    expect(container.textContent).toContain('$50')
    expect(container.textContent).toContain('$40')
  })

  it('leaves currency inside inline code and code blocks untouched', () => {
    const text = 'Run `price=$100` or check:\n```bash\nTOTAL=$200\n```'
    const { container } = render(
      <MarkdownWithCitations conversationId="conv-test">{text}</MarkdownWithCitations>,
    )
    expect(container.querySelector('.katex')).toBeNull()
    expect(container.textContent).toContain('price=$100')
    expect(container.textContent).toContain('TOTAL=$200')
  })
})

describe('escapeCurrencyDollars and containsMath helpers', () => {
  it('escapes unescaped dollars followed by digits or decimal numbers', () => {
    expect(escapeCurrencyDollars('Cost is $100 and tax is $5.50 or $.99')).toBe(
      'Cost is \\$100 and tax is \\$5.50 or \\$.99',
    )
    expect(escapeCurrencyDollars('Already escaped: \\$100')).toBe('Already escaped: \\$100')
    expect(escapeCurrencyDollars('售价$100元，或者 $50，最低$20。')).toBe(
      '售价\\$100元，或者 \\$50，最低\\$20。',
    )
  })

  it('does not escape double dollar blocks ($$1 + 1 = 2$$)', () => {
    expect(escapeCurrencyDollars('$$1 + 1 = 2$$')).toBe('$$1 + 1 = 2$$')
  })

  it('does not escape dollars inside inline code or fenced code blocks', () => {
    expect(escapeCurrencyDollars('`echo $100` and ```\n$200\n``` but $300 outside')).toBe(
      '`echo $100` and ```\n$200\n``` but \\$300 outside',
    )
  })

  it('preserves true inline math expressions', () => {
    expect(escapeCurrencyDollars('Formula: $x = \\pi$ and $E=mc^2$')).toBe(
      'Formula: $x = \\pi$ and $E=mc^2$',
    )
    expect(escapeCurrencyDollars('Constants: $e$, $i$, $\\pi$, $1$, $0$')).toBe(
      'Constants: $e$, $i$, $\\pi$, $1$, $0$',
    )
    expect(escapeCurrencyDollars('$1 + 1 = 2$ and $2\\pi$ and $100$')).toBe(
      '$1 + 1 = 2$ and $2\\pi$ and $100$',
    )
  })

  it('correctly detects whether text contains math', () => {
    expect(containsMath('Plain text without math')).toBe(false)
    expect(containsMath('Cost is \\$100 to \\$200')).toBe(false)
    expect(containsMath('`echo $a and $b` in code')).toBe(false)
    expect(containsMath('Formula $$E=mc^2$$ in block')).toBe(true)
    expect(containsMath('Inline formula $x = \\pi$ here')).toBe(true)
  })
})
