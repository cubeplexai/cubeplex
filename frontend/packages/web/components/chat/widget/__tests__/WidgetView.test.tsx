import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { WidgetView } from '../WidgetView'

describe('WidgetView', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('falls back to a source block when widget_code exceeds the size cap', () => {
    const big = 'x'.repeat(256 * 1024 + 1)
    render(<WidgetView widgetId="a" widgetCode={big} status="complete" />)
    expect(screen.getByText(/too large/i)).toBeInTheDocument()
  })

  it('falls back when no ready arrives before the timeout', () => {
    render(<WidgetView widgetId="a" widgetCode="<p>x</p>" status="complete" />)
    act(() => {
      vi.advanceTimersByTime(5001)
    })
    expect(screen.getByText(/failed to render/i)).toBeInTheDocument()
  })

  it('posts a morph only after a ready from the iframe (ignores forged source)', () => {
    const { container } = render(
      <WidgetView widgetId="a" widgetCode="<p>x</p>" status="complete" />,
    )
    const iframe = container.querySelector('iframe') as HTMLIFrameElement
    const post = vi.spyOn(iframe.contentWindow as Window, 'postMessage')

    // forged source (window, not the iframe) -> ignored
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', { data: { widgetId: 'a', type: 'ready' }, source: window }),
      )
    })
    expect(post).not.toHaveBeenCalled()

    // real ready (source === iframe.contentWindow) -> morph is sent
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          data: { widgetId: 'a', type: 'ready' },
          source: iframe.contentWindow,
        }),
      )
    })
    expect(post).toHaveBeenCalledWith(expect.objectContaining({ type: 'morph' }), '*')
  })
})
