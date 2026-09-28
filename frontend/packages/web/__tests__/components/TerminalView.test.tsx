import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TerminalView } from '@/components/panel/TerminalView'

vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }))

describe('Command request inspection', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('copies the exact command and retains all request fields in the raw request', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    const args = {
      description: 'Check the build',
      command: 'cd /workspace\nprintf "a\\nb"\npnpm build',
      timeout_seconds: 120,
      background: false,
      notify_on_complete: false,
      custom_option: { keep: true },
    }
    render(<TerminalView args={args} result={'Built successfully\n[exit: 0]'} />)

    fireEvent.click(screen.getByRole('button', { name: 'copyCommand' }))
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(args.command))
    expect(screen.getByText('foreground')).toBeVisible()
    expect(screen.getByText('disabled')).toBeVisible()

    fireEvent.click(screen.getByText('rawRequest'))
    fireEvent.click(screen.getByRole('button', { name: 'copyRequest' }))
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(JSON.stringify(args, null, 2)))
    expect(screen.getByText('Built successfully')).toBeVisible()
  })
})
