import { act, fireEvent, render, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ReactMarkdown from 'react-markdown'
import { MarkdownWithCitations } from '../MarkdownWithCitations'
import { markdownComponents } from '../markdownComponents'

const mocks = vi.hoisted(() => ({
  render: vi.fn(),
  initialize: vi.fn(),
  theme: 'light',
}))

vi.mock('mermaid', () => ({
  default: { render: mocks.render, initialize: mocks.initialize },
}))
vi.mock('next-themes', () => ({ useTheme: () => ({ resolvedTheme: mocks.theme }) }))

const source = 'flowchart LR\n  A[Start] --> B[Finish]'
const fenced = (value: string) => '```mermaid\n' + value + '\n```'
const svg = '<svg role="img"><text>Start → Finish</text></svg>'

beforeEach(() => {
  mocks.render.mockReset().mockResolvedValue({ svg })
  mocks.initialize.mockClear()
  mocks.theme = 'light'
})

describe('Markdown Mermaid rendering', () => {
  it('supports standalone Markdown views without the conversation store wrapper', async () => {
    const { container } = render(
      <ReactMarkdown components={markdownComponents}>{fenced(source)}</ReactMarkdown>,
    )
    await waitFor(() => expect(container.querySelector('svg')).not.toBeNull())
    expect(container.querySelector('pre')).toBeNull()
  })

  it('preserves sandbox navigation alongside diagrams and citations', async () => {
    const onNavigate = vi.fn()
    const { container, getByText } = render(
      <MarkdownWithCitations
        sandbox={{ filePath: '/workspace/readme.md', onNavigate, resolveAssetUrl: (path) => path }}
      >
        {fenced(source) + '\n\n[Details](details.md) 【1-1】'}
      </MarkdownWithCitations>,
    )
    await waitFor(() => expect(container.querySelector('svg')).not.toBeNull())
    fireEvent.click(getByText('Details'))
    expect(onNavigate).toHaveBeenCalledWith('/workspace/details.md')
  })

  it.each(['', '\n\nA reference 【1-1】'])(
    'renders diagram source with citations %s',
    async (suffix) => {
      const { container } = render(
        <MarkdownWithCitations>{fenced(source) + suffix}</MarkdownWithCitations>,
      )
      await waitFor(() => expect(container.querySelector('svg')).not.toBeNull())
      expect(mocks.render).toHaveBeenCalledWith(
        expect.any(String),
        source + '\n',
        expect.any(HTMLElement),
      )
      expect(container.querySelector('pre')).toBeNull()
      expect(mocks.initialize).toHaveBeenCalledWith(
        expect.objectContaining({ securityLevel: 'strict', startOnLoad: false }),
      )
    },
  )

  it('preserves ordinary code blocks and inline code', () => {
    const { container } = render(
      <MarkdownWithCitations>{'`mermaid`\n\n```js\nconst x = 1\n```'}</MarkdownWithCitations>,
    )
    expect(container.querySelector('pre code')?.textContent).toBe('const x = 1\n')
    expect(container.querySelector('p code')?.textContent).toBe('mermaid')
    expect(mocks.render).not.toHaveBeenCalled()
  })

  it('keeps invalid source readable and retries when streaming supplies corrected source', async () => {
    mocks.render.mockRejectedValueOnce(new Error('Incomplete diagram'))
    const { container, rerender } = render(
      <MarkdownWithCitations>{fenced('flowchart LR\n A[')}</MarkdownWithCitations>,
    )
    await waitFor(() => expect(mocks.render).toHaveBeenCalledTimes(1))
    expect(container.querySelector('pre code')?.textContent).toContain('A[')
    rerender(<MarkdownWithCitations>{fenced(source)}</MarkdownWithCitations>)
    await waitFor(() => expect(container.querySelector('svg')).not.toBeNull())
    expect(container.querySelector('pre')).toBeNull()
  })

  it('discards stale async output and rerenders for the current theme', async () => {
    let finish: ((value: { svg: string }) => void) | undefined
    mocks.render.mockImplementationOnce(
      () =>
        new Promise<{ svg: string }>((resolve) => {
          finish = resolve
        }),
    )
    const { container, rerender } = render(
      <MarkdownWithCitations>{fenced(source)}</MarkdownWithCitations>,
    )
    await waitFor(() => expect(finish).toBeDefined())
    mocks.theme = 'dark'
    const updated = source.replace('Finish', 'Updated')
    rerender(<MarkdownWithCitations>{fenced(updated)}</MarkdownWithCitations>)
    await act(async () => {
      finish?.({ svg: '<svg><text>Stale</text></svg>' })
    })
    await waitFor(() => expect(mocks.render).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(container.querySelector('svg')).not.toBeNull())
    expect(container.textContent).not.toContain('Stale')
    expect(mocks.initialize).toHaveBeenLastCalledWith(expect.objectContaining({ theme: 'dark' }))
    expect(mocks.render.mock.calls[1][1]).toBe(updated + '\n')
  })
})
