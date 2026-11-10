import { fireEvent, render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it } from 'vitest'
import type { TodoItem } from '@cubeplex/core'
import en from '../../messages/en.json'
import { TaskProgressStrip } from '../../components/chat/TaskProgressStrip'

const active: TodoItem[] = [
  { id: 'task-1', description: 'Inspect the current behavior', status: 'in_progress' },
  { id: 'task-2', description: 'Update the progress header', status: 'pending' },
  { id: 'task-3', description: 'Verify the interaction', status: 'completed' },
]

function renderStrip(todos: TodoItem[], conversationId: string, liveNonce = 0) {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      <TaskProgressStrip conversationId={conversationId} todos={todos} liveNonce={liveNonce} />
    </NextIntlClientProvider>,
  )
}

describe('TaskProgressStrip', () => {
  it('keeps the current step in the header and folds completed items', () => {
    renderStrip(active, 'conv-header')

    const toggle = screen.getByRole('button', { name: /Inspect the current behavior/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText('Inspect the current behavior')).toHaveAttribute('aria-live', 'polite')
    expect(screen.getByText('1/3 completed')).toBeInTheDocument()
    expect(screen.queryByText('Update the progress header')).not.toBeInTheDocument()
    expect(screen.queryByText('Verify the interaction')).not.toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '1')

    fireEvent.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Inspect the current behavior')).toBeInTheDocument()
    expect(screen.getByRole('list')).toHaveTextContent('Update the progress header')
    expect(screen.queryByText('Verify the interaction')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '1 completed' }))

    const done = screen.getByText('Verify the interaction')
    expect(done).toBeInTheDocument()
    expect(done).not.toHaveClass('line-through')
  })

  it('stays collapsed when a restored list replaces the empty placeholder', () => {
    const { rerender } = renderStrip([], 'conv-cold')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()

    rerender(
      <NextIntlClientProvider locale="en" messages={en}>
        <TaskProgressStrip conversationId="conv-cold" todos={active} liveNonce={0} />
      </NextIntlClientProvider>,
    )

    expect(screen.getByRole('button', { name: /Inspect the current behavior/ })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
  })

  it('opens only when a live write fills an empty list', () => {
    const { rerender } = renderStrip([], 'conv-appear')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()

    rerender(
      <NextIntlClientProvider locale="en" messages={en}>
        <TaskProgressStrip conversationId="conv-appear" todos={active} liveNonce={1} />
      </NextIntlClientProvider>,
    )

    expect(screen.getByRole('button', { name: /Inspect the current behavior/ })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
  })

  it('shows the only open step when the list would otherwise be empty', () => {
    renderStrip(
      [{ id: 'task-1', description: 'Inspect the current behavior', status: 'in_progress' }],
      'conv-single',
    )

    fireEvent.click(screen.getByRole('button', { name: /Inspect the current behavior/ }))

    expect(screen.getByRole('list')).toHaveTextContent('Inspect the current behavior')
  })

  it('stays collapsed when a later status update arrives', () => {
    const { rerender } = renderStrip(active, 'conv-stay')
    const toggle = screen.getByRole('button', { name: /Inspect the current behavior/ })
    fireEvent.click(toggle)
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')

    const next: TodoItem[] = [
      { id: 'task-1', description: 'Inspect the current behavior', status: 'completed' },
      { id: 'task-2', description: 'Update the progress header', status: 'in_progress' },
      { id: 'task-3', description: 'Verify the interaction', status: 'completed' },
    ]
    rerender(
      <NextIntlClientProvider locale="en" messages={en}>
        <TaskProgressStrip conversationId="conv-stay" todos={next} liveNonce={1} />
      </NextIntlClientProvider>,
    )

    const nextToggle = screen.getByRole('button', { name: /Update the progress header/ })
    expect(nextToggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Inspect the current behavior')).not.toBeInTheDocument()
  })

  it('remembers an open list for the conversation', () => {
    const first = renderStrip(active, 'conv-memory')
    fireEvent.click(screen.getByRole('button', { name: /Inspect the current behavior/ }))
    first.unmount()

    renderStrip(active, 'conv-memory')
    expect(screen.getByRole('button', { name: /Inspect the current behavior/ })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
  })

  it('shows a quiet done line when every item is complete', () => {
    const done: TodoItem[] = [
      { id: 'task-1', description: 'Inspect the current behavior', status: 'completed' },
      { id: 'task-2', description: 'Update the progress header', status: 'completed' },
    ]
    renderStrip(done, 'conv-done')

    expect(screen.getByRole('button', { name: '2/2 tasks done' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
    expect(screen.queryByText('2/2 completed')).not.toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '2')
  })
})
