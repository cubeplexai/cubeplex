import { fireEvent, render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, vi } from 'vitest'
import en from '../../messages/en.json'
import { CommandPopover } from '../../components/chat/CommandPopover'
import type { SlashCommand } from '../../lib/slash-commands'

const cmds: SlashCommand[] = [
  {
    id: 'new',
    name: 'new',
    descriptionKey: 'commands.new.description',
    category: 'conversation',
    isAvailable: () => true,
    run: vi.fn(),
  },
  {
    id: 'help',
    name: 'help',
    descriptionKey: 'commands.help.description',
    category: 'help',
    isAvailable: () => true,
    run: vi.fn(),
  },
]

function renderPopover(props: Partial<React.ComponentProps<typeof CommandPopover>> = {}) {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      <CommandPopover
        open
        commands={cmds}
        activeIndex={0}
        onActiveIndexChange={vi.fn()}
        onSelect={vi.fn()}
        {...props}
      />
    </NextIntlClientProvider>,
  )
}

describe('CommandPopover', () => {
  it('renders command rows with listbox roles', () => {
    renderPopover()
    const list = screen.getByTestId('slash-command-popover')
    expect(list).toHaveAttribute('role', 'listbox')
    expect(screen.getByTestId('slash-cmd-new')).toHaveAttribute('role', 'option')
    expect(screen.getByText('/new')).toBeInTheDocument()
  })

  it('calls onSelect when a row is clicked', () => {
    const onSelect = vi.fn()
    renderPopover({ onSelect })
    fireEvent.click(screen.getByTestId('slash-cmd-help'))
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'help' }))
  })

  it('shows empty state when no commands', () => {
    renderPopover({ commands: [] })
    expect(screen.getByText('No matching commands')).toBeInTheDocument()
  })
})
