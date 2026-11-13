import { fireEvent, render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, vi } from 'vitest'
import AdminMembersPage from '@/app/admin/members/page'
import messages from '@/messages/en.json'

const mocks = vi.hoisted(() => ({
  createOrgInvite: vi.fn(async () => ({
    token: 'tok',
    expires_at: '2026-11-14T00:00:00Z',
    role: 'member',
    invite_url: 'http://localhost:3039/orgs/invites/accept?token=tok',
    email: 'new@example.com',
    email_sent: true,
  })),
}))

vi.mock('@cubeplex/core', () => ({
  createApiClient: () => ({}),
  createOrgInvite: mocks.createOrgInvite,
  ApiError: class ApiError extends Error {},
  useMemberStore: () => ({
    orgMembers: [],
    orgLoading: false,
    loadOrgMembers: vi.fn(),
    updateOrgMemberRole: vi.fn(),
    removeOrgMember: vi.fn(),
  }),
}))

describe('Admin members page', () => {
  it('sends an email invite and does not add the person immediately', async () => {
    mocks.createOrgInvite.mockClear()
    render(
      <NextIntlClientProvider locale="en" messages={messages}>
        <AdminMembersPage />
      </NextIntlClientProvider>,
    )

    expect(screen.queryByRole('button', { name: 'Add member' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Invite member' }))
    fireEvent.change(screen.getByLabelText('Email address'), {
      target: { value: 'new@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send invite' }))

    expect(mocks.createOrgInvite).toHaveBeenCalledWith({}, 'member', 'new@example.com')
    expect(
      await screen.findByText(
        'Invitation sent to new@example.com. They are not a member until they accept.',
      ),
    ).toBeInTheDocument()
  })
})
