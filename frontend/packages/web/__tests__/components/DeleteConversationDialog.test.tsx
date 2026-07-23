import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import en from '../../messages/en.json'
import { DeleteConversationDialog } from '@/components/layout/DeleteConversationDialog'

const remove = vi.fn()
const toastError = vi.fn()
const routerReplace = vi.fn()
let pathname = '/w/ws-1'

vi.mock('sonner', () => ({
  toast: {
    error: (...args: unknown[]) => toastError(...args),
    success: vi.fn(),
  },
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: routerReplace }),
  usePathname: () => pathname,
}))

vi.mock('@cubeplex/core', () => ({
  createApiClient: () => ({
    setWorkspaceId: vi.fn(),
  }),
  useConversationStore: (selector: (s: { remove: typeof remove }) => unknown) =>
    selector({ remove }),
}))

function renderDialog(props: Partial<React.ComponentProps<typeof DeleteConversationDialog>> = {}) {
  const onOpenChange = vi.fn()
  const view = render(
    <NextIntlClientProvider locale="en" messages={en}>
      <DeleteConversationDialog
        open
        onOpenChange={onOpenChange}
        conversationId="conv-1"
        conversationTitle="Sprint planning"
        currentWsId="ws-1"
        {...props}
      />
    </NextIntlClientProvider>,
  )
  return { ...view, onOpenChange }
}

describe('DeleteConversationDialog', () => {
  beforeEach(() => {
    remove.mockReset()
    toastError.mockReset()
    routerReplace.mockReset()
    pathname = '/w/ws-1'
  })

  it('shows title and consequence copy with the conversation title', () => {
    renderDialog()

    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    expect(screen.getByText('Delete conversation?')).toBeInTheDocument()
    expect(
      screen.getByText(
        /"Sprint planning" will be removed from your history\. Messages will no longer be available in this conversation\. Related artifacts will no longer appear in the library or the conversation panel\./,
      ),
    ).toBeInTheDocument()
  })

  it('uses the untitled fallback when the title is empty', () => {
    renderDialog({ conversationTitle: '' })

    expect(
      screen.getByText(/"New conversation" will be removed from your history/),
    ).toBeInTheDocument()
  })

  it('Cancel does not call remove', () => {
    const { onOpenChange } = renderDialog()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(remove).not.toHaveBeenCalled()
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('Confirm calls remove once and closes on success', async () => {
    remove.mockResolvedValue(undefined)
    const { onOpenChange } = renderDialog()

    fireEvent.click(screen.getByTestId('conversation-delete-confirm'))

    await waitFor(() => {
      expect(remove).toHaveBeenCalledTimes(1)
    })
    expect(remove.mock.calls[0]?.[1]).toBe('conv-1')
    await waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false)
    })
    expect(toastError).not.toHaveBeenCalled()
    // Not viewing this conversation → no leave-route navigation.
    expect(routerReplace).not.toHaveBeenCalled()
  })

  it('navigates to workspace home when deleting the open conversation', async () => {
    pathname = '/w/ws-1/conversations/conv-1'
    remove.mockResolvedValue(undefined)
    renderDialog()

    fireEvent.click(screen.getByTestId('conversation-delete-confirm'))

    await waitFor(() => {
      expect(routerReplace).toHaveBeenCalledWith('/w/ws-1')
    })
  })

  it('does not navigate when the route changed while delete was in flight', async () => {
    pathname = '/w/ws-1/conversations/conv-1'
    let resolveRemove: (() => void) | undefined
    remove.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          resolveRemove = resolve
        }),
    )
    const { rerender } = renderDialog()

    fireEvent.click(screen.getByTestId('conversation-delete-confirm'))
    await waitFor(() => {
      expect(screen.getByTestId('conversation-delete-confirm')).toBeDisabled()
    })

    // User left the deleted conversation before DELETE finished.
    pathname = '/w/ws-1/conversations/other-conv'
    rerender(
      <NextIntlClientProvider locale="en" messages={en}>
        <DeleteConversationDialog
          open
          onOpenChange={vi.fn()}
          conversationId="conv-1"
          conversationTitle="Sprint planning"
          currentWsId="ws-1"
        />
      </NextIntlClientProvider>,
    )

    resolveRemove?.()
    await waitFor(() => {
      expect(remove).toHaveBeenCalledTimes(1)
    })
    // Give the success path a tick; must not bounce the user off other-conv.
    await waitFor(() => {
      expect(screen.queryByText('Deleting…')).not.toBeInTheDocument()
    })
    expect(routerReplace).not.toHaveBeenCalled()
  })

  it('does not navigate when delete fails on the open conversation', async () => {
    pathname = '/w/ws-1/conversations/conv-1'
    remove.mockRejectedValue(new Error('network down'))
    renderDialog()

    fireEvent.click(screen.getByTestId('conversation-delete-confirm'))

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled()
    })
    expect(routerReplace).not.toHaveBeenCalled()
  })

  it('keeps the dialog open and toasts when remove rejects', async () => {
    remove.mockRejectedValue(new Error('network down'))
    const { onOpenChange } = renderDialog()

    fireEvent.click(screen.getByTestId('conversation-delete-confirm'))

    await waitFor(() => {
      expect(remove).toHaveBeenCalledTimes(1)
    })
    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(
        'Failed to delete conversation',
        expect.objectContaining({ description: 'network down' }),
      )
    })
    // Failure must not close the dialog (store does not drop the row either).
    expect(onOpenChange).not.toHaveBeenCalledWith(false)
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    // Confirm re-enabled after failure so the user can retry.
    expect(screen.getByTestId('conversation-delete-confirm')).not.toBeDisabled()
  })

  it('disables Confirm while remove is in flight', async () => {
    let resolveRemove: (() => void) | undefined
    remove.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          resolveRemove = resolve
        }),
    )
    renderDialog()

    fireEvent.click(screen.getByTestId('conversation-delete-confirm'))

    await waitFor(() => {
      expect(screen.getByTestId('conversation-delete-confirm')).toBeDisabled()
    })
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    expect(screen.getByText('Deleting…')).toBeInTheDocument()

    resolveRemove?.()
    await waitFor(() => {
      expect(screen.queryByText('Deleting…')).not.toBeInTheDocument()
    })
  })
})
