import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { NextIntlClientProvider } from 'next-intl'
import type { IntlError } from 'use-intl'
import { RunErrorBubble } from '@/components/chat/RunErrorBubble'

// Minimal runError namespace — only the keys exercised by these tests.
// We intentionally omit unknown error codes to trigger the fallback path.
const messages = {
  runError: {
    context_length_exceeded:
      "Conversation exceeded {model}'s context window ({tokens_in, number} / {context_window, number} tokens). Start a new chat or switch models.",
    rate_limited: 'Rate limit hit for {model}. Wait a moment, then try again.',
  },
}

// next-intl 4.x does NOT throw on missing keys by default — it calls onError
// (console.error) and returns the key string as a fallback. RunErrorBubble's
// try/catch fallback therefore requires `onError: (e) => { throw e }` to
// exercise the catch branch. Pass this to the "missing key" tests only.
function renderWithIntl(node: React.ReactNode, locale = 'en') {
  return render(
    <NextIntlClientProvider locale={locale} messages={messages}>
      {node}
    </NextIntlClientProvider>,
  )
}

function renderWithIntlThrowOnMissing(node: React.ReactNode, locale = 'en') {
  return render(
    <NextIntlClientProvider
      locale={locale}
      messages={messages}
      onError={(e: IntlError) => {
        throw e
      }}
    >
      {node}
    </NextIntlClientProvider>,
  )
}

describe('RunErrorBubble', () => {
  it('renders localized message with interpolated params', () => {
    renderWithIntl(
      <RunErrorBubble
        data={{
          error_code: 'context_length_exceeded',
          params: { model: 'kimi-k2.6', tokens_in: 262014, context_window: 256000 },
          message: 'fallback not used here',
        }}
      />,
    )
    const alert = screen.getByRole('alert')
    // next-intl formats numbers per locale (en-US default): 262,014 / 256,000
    expect(alert).toHaveTextContent(/kimi-k2\.6/)
    expect(alert).toHaveTextContent(/262,014/)
    expect(alert).toHaveTextContent(/256,000/)
    // The raw fallback text must NOT appear when a translation key exists.
    expect(alert).not.toHaveTextContent('fallback not used here')
  })

  it('falls back to data.message when i18n key is missing', () => {
    // Use the throwing provider so next-intl's missing-key IntlError propagates
    // into RunErrorBubble's try/catch, which then falls back to data.message.
    renderWithIntlThrowOnMissing(
      <RunErrorBubble
        data={{
          error_code: 'some_brand_new_code_we_havent_translated',
          message: 'The raw English fallback line from the backend.',
        }}
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent(
      /The raw English fallback line from the backend\./,
    )
  })

  it('renders the alert role for accessibility', () => {
    renderWithIntl(
      <RunErrorBubble
        data={{
          error_code: 'rate_limited',
          params: { model: 'claude-sonnet-4-6' },
          message: 'fallback not used here',
        }}
      />,
    )
    const alert = screen.getByRole('alert')
    expect(alert).toBeVisible()
    expect(alert).toHaveTextContent(/claude-sonnet-4-6/)
  })

  it('renders without params when i18n key is missing (no params field)', () => {
    renderWithIntlThrowOnMissing(
      <RunErrorBubble
        data={{
          error_code: 'some_unknown_code',
          message: 'Something went wrong with no params.',
        }}
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong with no params.')
  })
})
