import { fireEvent, render, screen, within } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it } from 'vitest'
import type { TodoItem } from '@cubeplex/core'
import en from '../../messages/en.json'
import { TaskProgressCard } from '../../components/chat/TaskProgressCard'

const todos: TodoItem[] = [
  { id: 'task-1', description: 'Inspect the current behavior', status: 'in_progress' },
  { id: 'task-2', description: 'Update the progress header', status: 'pending' },
  { id: 'task-3', description: 'Verify the interaction', status: 'pending' },
]

describe('TaskProgressCard header', () => {
  it('shows the overview when expanded and the active task when collapsed', () => {
    render(
      <NextIntlClientProvider locale="en" messages={en}>
        <TaskProgressCard todos={todos} isStreaming={false} />
      </NextIntlClientProvider>,
    )

    const toggle = screen.getByRole('button')
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(within(toggle).getByText('Task progress')).toBeInTheDocument()
    expect(within(toggle).getByText('0/3 completed')).toBeInTheDocument()

    fireEvent.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(within(toggle).getByText('Inspect the current behavior')).toBeInTheDocument()
    expect(within(toggle).getByText('0/3 completed')).toBeInTheDocument()
  })
})
