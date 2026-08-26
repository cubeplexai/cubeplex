import { render, screen, fireEvent, within, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { usePanelStore } from '@cubeplex/core'

let liveViewUrl = 'https://neko.example/live'
const refreshLiveView = vi.fn<() => Promise<{ url: string } | undefined>>()

vi.mock('@/hooks/useBrowserLiveView', () => ({
  useBrowserLiveView: () => ({
    url: liveViewUrl,
    loading: false,
    validating: false,
    error: undefined,
    refresh: refreshLiveView,
  }),
}))

vi.mock('@/lib/csrf', () => ({
  csrfHeaders: () => ({}),
}))

import { BrowserView } from '@/components/panel/BrowserView'

const messages = {
  panel: {
    header: {
      copy: 'Copy',
      close: 'Close',
      expand: 'Expand preview',
      exitExpand: 'Exit expand',
    },
  },
}

function renderView(hideHeader = true) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <div style={{ width: 800, height: 600 }}>
        <BrowserView workspaceId="ws-1" hideHeader={hideHeader} />
      </div>
    </NextIntlClientProvider>,
  )
}

describe('BrowserView expand theater', () => {
  beforeEach(() => {
    liveViewUrl = 'https://neko.example/live'
    refreshLiveView.mockReset()
    refreshLiveView.mockResolvedValue({ url: liveViewUrl })
    vi.stubGlobal(
      'matchMedia',
      (query: string) =>
        ({
          matches: query.includes('min-width: 768px'),
          media: query,
          onchange: null,
          addEventListener: vi.fn(),
          removeEventListener: vi.fn(),
          addListener: vi.fn(),
          removeListener: vi.fn(),
          dispatchEvent: vi.fn(),
        }) as MediaQueryList,
    )
    // ResizeObserver used by aspect-fit frame
    vi.stubGlobal(
      'ResizeObserver',
      class {
        cb: ResizeObserverCallback
        constructor(cb: ResizeObserverCallback) {
          this.cb = cb
        }
        observe(el: Element) {
          const rect = {
            width: 800,
            height: 600,
            top: 0,
            left: 0,
            bottom: 600,
            right: 800,
            x: 0,
            y: 0,
            toJSON: () => ({}),
          }
          this.cb(
            [
              {
                target: el,
                contentRect: rect,
                borderBoxSize: [],
                contentBoxSize: [],
                devicePixelContentBoxSize: [],
              },
            ],
            this as unknown as ResizeObserver,
          )
        }
        unobserve() {}
        disconnect() {}
      },
    )
    usePanelStore.setState({ view: { type: 'sandbox' } })
  })

  it('opens expand with dialog focus trap and keeps the same Neko iframe', async () => {
    renderView()
    expect(screen.getByTestId('browser-rail')).toBeInTheDocument()
    const iframeBefore = await waitFor(() => screen.getByTitle('Sandbox browser'))

    fireEvent.click(screen.getByTestId('panel-expand'))

    const dialog = await screen.findByRole('dialog')
    expect(dialog).toBeInTheDocument()
    expect(screen.getByTestId('browser-expand-preview')).toBeInTheDocument()
    expect(screen.getByTestId('browser-rail-placeholder')).toBeInTheDocument()
    // Same single iframe — WebRTC peer must not be torn down.
    expect(screen.getAllByTitle('Sandbox browser')).toHaveLength(1)
    expect(screen.getByTitle('Sandbox browser')).toBe(iframeBefore)
    // Host moved into the theater slot.
    expect(within(screen.getByTestId('browser-expand-preview')).getByTitle('Sandbox browser')).toBe(
      iframeBefore,
    )
  })

  it('exit expand reparents iframe to rail and keeps sandbox panel open', async () => {
    renderView()
    await waitFor(() => screen.getByTitle('Sandbox browser'))
    fireEvent.click(screen.getByTestId('panel-expand'))
    await screen.findByRole('dialog')
    const iframe = screen.getByTitle('Sandbox browser')

    fireEvent.click(within(screen.getByRole('dialog')).getByTestId('panel-exit-expand'))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByTestId('browser-rail')).toBeInTheDocument()
    expect(screen.getByTitle('Sandbox browser')).toBe(iframe)
    expect(usePanelStore.getState().view.type).toBe('sandbox')
  })

  it('Esc closes theater and keeps panel', async () => {
    renderView()
    await waitFor(() => screen.getByTitle('Sandbox browser'))
    fireEvent.click(screen.getByTestId('panel-expand'))
    const dialog = await screen.findByRole('dialog')

    fireEvent.keyDown(dialog, { key: 'Escape' })

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    expect(screen.getByTestId('browser-rail')).toBeInTheDocument()
    expect(usePanelStore.getState().view.type).toBe('sandbox')
  })

  it('aspect-fit frame is landscape (not stretched to tall rail)', async () => {
    renderView()
    const frame = await waitFor(() => {
      const iframe = screen.getByTitle('Sandbox browser')
      const host = iframe.parentElement
      expect(host).toBeTruthy()
      return host!
    })
    // 800×600 parent → largest 1280:900 box is 800 × 562.5 → rounded
    expect(frame.style.width).toBe('800px')
    expect(frame.style.height).toBe('563px')
  })

  it('refreshes a changed signed URL once and resets takeover', async () => {
    refreshLiveView.mockImplementation(async () => {
      liveViewUrl = 'https://neko.example/live-new-token'
      return { url: liveViewUrl }
    })
    renderView(false)
    const iframe = await waitFor(() => screen.getByTitle('Sandbox browser'))

    fireEvent.click(screen.getByRole('button', { name: 'Take over' }))
    expect(screen.getByRole('button', { name: 'Hand back to agent' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Refresh live view' }))

    await waitFor(() => {
      expect(screen.getByTitle('Sandbox browser')).toHaveAttribute(
        'src',
        'https://neko.example/live-new-token',
      )
    })
    expect(screen.getByTitle('Sandbox browser')).toBe(iframe)
    expect(screen.getByRole('button', { name: 'Take over' })).toBeInTheDocument()
  })

  it('coalesces refresh clicks while endpoint resolution is in flight', async () => {
    let resolveRefresh: ((value: { url: string }) => void) | undefined
    refreshLiveView.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRefresh = resolve
        }),
    )
    renderView(false)
    await waitFor(() => screen.getByTitle('Sandbox browser'))
    const refreshButton = screen.getByRole('button', { name: 'Refresh live view' })

    fireEvent.click(refreshButton)
    fireEvent.click(refreshButton)

    expect(refreshLiveView).toHaveBeenCalledTimes(1)
    resolveRefresh?.({ url: liveViewUrl })
    await waitFor(() => expect(refreshButton).not.toBeDisabled())
  })

  it('remounts once when refresh returns the same signed URL', async () => {
    renderView(false)
    const iframe = await waitFor(() => screen.getByTitle('Sandbox browser'))

    fireEvent.click(screen.getByRole('button', { name: 'Refresh live view' }))

    await waitFor(() => expect(screen.getByTitle('Sandbox browser')).not.toBe(iframe))
    expect(refreshLiveView).toHaveBeenCalledTimes(1)
  })
})
