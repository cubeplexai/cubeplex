import { act, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { WebFetchView } from '../WebFetchView'

const mocks = vi.hoisted(() => ({ render: vi.fn() }))
vi.mock('mermaid', () => ({ default: { initialize: vi.fn(), render: mocks.render } }))
vi.mock('next-themes', () => ({ useTheme: () => ({ resolvedTheme: 'light' }) }))

const source = 'flowchart LR\n A[Start] --> B[Finish]'
const result = '```mermaid\n' + source + '\n```'
const svg = '<svg><text>Start → Finish</text></svg>'
const scrollIntoView = vi.fn()
const originalScrollIntoView = HTMLElement.prototype.scrollIntoView

beforeEach(() => {
  mocks.render.mockReset().mockResolvedValue({ svg })
  scrollIntoView.mockClear()
  HTMLElement.prototype.scrollIntoView = scrollIntoView
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    callback(0)
    return 0
  })
})
afterEach(() => {
  HTMLElement.prototype.scrollIntoView = originalScrollIntoView
  vi.unstubAllGlobals()
})

it.each([source, result])(
  'preserves citation highlighting through SVG replacement: %s',
  async (citation) => {
    let finish: ((value: { svg: string }) => void) | undefined
    mocks.render.mockImplementationOnce(
      () =>
        new Promise<{ svg: string }>((resolve) => {
          finish = resolve
        }),
    )
    const { container } = render(
      <WebFetchView args={{}} result={result} highlightText={citation} highlightKey={1} />,
    )
    await waitFor(() => expect(finish).toBeDefined())
    const highlighted = container.querySelector('.ring-2')
    expect(highlighted).not.toBeNull()
    await act(async () => {
      finish?.({ svg })
    })
    await waitFor(() => expect(container.querySelector('svg')).not.toBeNull())
    expect(highlighted?.isConnected).toBe(true)
    expect(highlighted).toHaveClass('ring-2')
  },
)

it.each([source, result])(
  'finds rendered diagrams and clears the previous citation: %s',
  async (citation) => {
    const { container, rerender } = render(<WebFetchView args={{}} result={result} />)
    await waitFor(() => expect(container.querySelector('svg')).not.toBeNull())
    rerender(<WebFetchView args={{}} result={result} highlightText={citation} highlightKey={1} />)
    const highlighted = container.querySelector('.ring-2')
    expect(highlighted).not.toBeNull()
    expect(scrollIntoView).toHaveBeenCalled()
    rerender(
      <WebFetchView
        args={{}}
        result={result}
        highlightText="unrelated citation"
        highlightKey={2}
      />,
    )
    expect(highlighted).not.toHaveClass('ring-2')
  },
)

it('still highlights ordinary text after a rendered diagram', async () => {
  const text = 'An ordinary citation after the diagram'
  const content = result + '\n\n' + text
  const { container, rerender } = render(<WebFetchView args={{}} result={content} />)
  await waitFor(() => expect(container.querySelector('svg')).not.toBeNull())
  rerender(<WebFetchView args={{}} result={content} highlightText={text} highlightKey={1} />)
  expect(container.querySelector('p')).toHaveClass('ring-2')
  expect(container.querySelector('[data-mermaid-source]')).not.toHaveClass('ring-2')
})
