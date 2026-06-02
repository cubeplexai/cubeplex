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
