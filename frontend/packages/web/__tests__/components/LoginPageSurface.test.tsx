import { fireEvent, render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, vi } from 'vitest'
import AuthLayout from '@/app/(auth)/layout'
import { LoginForm } from '@/components/auth/LoginForm'
import messages from '@/messages/en.json'

const push = vi.fn()
const refresh = vi.fn()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, refresh }),
}))

vi.mock('@cubebox/core/hooks/useDeploymentMode', () => ({
  useDeploymentMode: () => ({
    mode: 'multi_tenant',
    loading: false,
    error: undefined,
    sandboxEnabled: true,
    passwordPolicy: 'high',
  }),
}))

describe('Login page surface', () => {
  it('pairs product narrative with the existing accessible login controls', () => {
    document.cookie = 'NEXT_LOCALE=; Max-Age=0; path=/'
    refresh.mockClear()

    render(
      <NextIntlClientProvider locale="en" messages={messages}>
        <AuthLayout>
          <LoginForm nextPath="/workspaces" />
        </AuthLayout>
      </NextIntlClientProvider>,
    )

    expect(screen.getByText(/agent workspace/i)).toBeInTheDocument()
    expect(screen.getByText(/memory, tools, and approvals/i)).toBeInTheDocument()
    expect(screen.getByTestId('auth-brand-logo')).toHaveTextContent('cubebox')
    expect(screen.getByTestId('auth-ambient-background')).toBeInTheDocument()
    expect(screen.getByTestId('cubebox-runtime-visual')).toBeInTheDocument()
    expect(screen.getByTestId('cubebox-runtime-visual')).toHaveAttribute(
      'data-visual',
      'animated-cube-background',
    )
    expect(screen.getByRole('combobox', { name: 'Language' })).toHaveValue('en')

    expect(screen.getByLabelText('Email')).toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /login with google/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sso login/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /forgot password/i })).toHaveAttribute(
      'href',
      '/forgot-password',
    )
    expect(screen.getByRole('link', { name: /create an account/i })).toHaveAttribute(
      'href',
      '/register?next=%2Fworkspaces',
    )

    fireEvent.change(screen.getByRole('combobox', { name: 'Language' }), {
      target: { value: 'zh' },
    })
    expect(document.cookie).toContain('NEXT_LOCALE=zh')
    expect(refresh).toHaveBeenCalledTimes(1)
  })
})
