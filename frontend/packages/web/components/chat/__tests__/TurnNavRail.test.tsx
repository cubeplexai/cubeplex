import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import en from '../../../messages/en.json'
import { TurnNavRail } from '../TurnNavRail'
import type { TurnNavItem } from '@/lib/turnNav'

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <NextIntlClientProvider locale="en" messages={en}>
      {children}
    </NextIntlClientProvider>
  )
}

const turns: TurnNavItem[] = [
  { id: 'a', userText: 'alpha question', assistantText: 'alpha answer', messageIds: ['a'] },
  { id: 'b', userText: 'beta question', assistantText: 'beta answer', messageIds: ['b'] },
  { id: 'c', userText: 'gamma question', assistantText: 'gamma answer', messageIds: ['c'] },
  { id: 'd', userText: 'delta question', assistantText: 'delta answer', messageIds: ['d'] },
]

function ticks(): HTMLElement[] {
  return screen.getAllByTestId('turn-nav-tick')
}

describe('TurnNavRail', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('grows the hovered tick immediately and the neighbors halfway', () => {
    render(<TurnNavRail turns={turns} onJump={() => undefined} />, { wrapper })

    for (const tick of ticks()) {
      expect(tick.style.width).toBe('4px')
      expect(tick.style.opacity).toBe('0.4')
      expect(tick.style.transition).toBe(
        'width 0.2s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.2s cubic-bezier(0.22, 1, 0.36, 1)',
      )
      expect(tick.style.transition).not.toContain('all')
    }

    fireEvent.mouseEnter(screen.getAllByRole('button')[2])

    expect(ticks()[2].style.width).toBe('16px')
    expect(ticks()[2].style.opacity).toBe('1')
    expect(ticks()[1].style.width).toBe('10px')
    expect(ticks()[3].style.width).toBe('10px')
    expect(ticks()[0].style.width).toBe('4px')
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  })

  it('opens the preview only after the pointer rests, and closes after it leaves', async () => {
    render(<TurnNavRail turns={turns} onJump={() => undefined} />, { wrapper })
    const buttons = screen.getAllByRole('button')

    fireEvent.mouseEnter(buttons[0])
    await act(async () => {
      await vi.advanceTimersByTimeAsync(89)
    })
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    const preview = screen.getByRole('tooltip')
    expect(preview).toHaveTextContent('You')
    expect(preview).toHaveTextContent('alpha question')
    expect(preview).toHaveTextContent('Agent')
    expect(preview).toHaveTextContent('alpha answer')

    fireEvent.mouseLeave(buttons[0])
    await act(async () => {
      await vi.advanceTimersByTimeAsync(79)
    })
    expect(screen.getByRole('tooltip')).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1 + 200)
    })
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  })

  it('does not flash a preview while the pointer scrubs across ticks', async () => {
    render(<TurnNavRail turns={turns} onJump={() => undefined} />, { wrapper })
    const buttons = screen.getAllByRole('button')

    fireEvent.mouseEnter(buttons[0])
    await act(async () => {
      await vi.advanceTimersByTimeAsync(40)
    })
    fireEvent.mouseLeave(buttons[0])
    fireEvent.mouseEnter(buttons[1])
    await act(async () => {
      await vi.advanceTimersByTimeAsync(40)
    })
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(90)
    })
    expect(screen.getByRole('tooltip')).toHaveTextContent('beta question')
    expect(screen.getByRole('tooltip')).toHaveTextContent('beta answer')
  })

  it('truncates long preview text and jumps when a tick is clicked', async () => {
    const onJump = vi.fn()
    const long = '问'.repeat(120)
    render(
      <TurnNavRail
        turns={[{ id: 'long', userText: long, assistantText: '', messageIds: ['long'] }]}
        onJump={onJump}
      />,
      { wrapper },
    )

    fireEvent.mouseEnter(screen.getByRole('button'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(90)
    })
    const preview = screen.getByRole('tooltip')
    expect(preview).toHaveTextContent(`${'问'.repeat(80)}...`)
    expect(preview).toHaveTextContent('No reply yet')

    fireEvent.click(screen.getByRole('button'))
    expect(onJump).toHaveBeenCalledWith(expect.objectContaining({ id: 'long' }))
  })

  it('loads earlier turns from the mark above the column and does not preview it', async () => {
    const onLoadEarlier = vi.fn()
    const { rerender } = render(
      <TurnNavRail turns={turns} onJump={() => undefined} onLoadEarlier={onLoadEarlier} />,
      { wrapper },
    )
    expect(screen.queryByRole('button', { name: 'Load earlier turns' })).not.toBeInTheDocument()

    rerender(
      <TurnNavRail turns={turns} onJump={() => undefined} hasOlder onLoadEarlier={onLoadEarlier} />,
    )
    const earlier = screen.getByRole('button', { name: 'Load earlier turns' })
    fireEvent.mouseEnter(earlier)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(90)
    })
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
    expect(ticks()[0].style.width).toBe('4px')

    fireEvent.click(earlier)
    expect(onLoadEarlier).toHaveBeenCalledOnce()

    rerender(
      <TurnNavRail
        turns={turns}
        onJump={() => undefined}
        hasOlder
        loadingOlder
        onLoadEarlier={onLoadEarlier}
      />,
    )
    expect(screen.getByRole('button', { name: 'Load earlier turns' })).toBeDisabled()
  })
})
