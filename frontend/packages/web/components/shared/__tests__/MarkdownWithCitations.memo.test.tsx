import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { MarkdownWithCitations } from '../MarkdownWithCitations'

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

  it('still renders $$…$$ display math as KaTeX', () => {
    const { container } = render(
      <MarkdownWithCitations conversationId="conv-test">
        {'E = mc^2 in $$E=mc^2$$ form'}
      </MarkdownWithCitations>,
    )
    expect(container.querySelector('.katex')).not.toBeNull()
  })
})
