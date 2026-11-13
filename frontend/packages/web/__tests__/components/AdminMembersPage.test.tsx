import { fireEvent, render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, vi } from 'vitest'
import AdminMembersPage from '@/app/admin/members/page'
import messages from '@/messages/en.json'

vi.mock('@cubeplex/core', () => ({
  createApiClient: () => ({}),
  createOrgInvite: vi.fn(),
  useMemberStore: () => ({
    orgMembers: [],
    orgLoading: false,
    loadOrgMembers: vi.fn(),
    updateOrgMemberRole: vi.fn(),
    removeOrgMember: vi.fn(),
  }),
}))

describe('Admin members page', () => {
  it('invites someone into the org instead of adding them by email', () => {
    render(
      <NextIntlClientProvider locale="en" messages={messages}>
        <AdminMembersPage />
      </NextIntlClientProvider>,
    )

    const addButton = screen.queryByRole('button', { name: 'Add member' })
    expect(addButton).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Invite member' }))

    expect(screen.getByTestId('create-org-invite-dialog')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create invite link' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Email address')).not.toBeInTheDocument()
  })
})
