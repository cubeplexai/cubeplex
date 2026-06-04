import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { NextIntlClientProvider } from 'next-intl'
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

// Default provider — no onError override. next-intl returns the key string on
// missing keys; RunErrorBubble detects this and falls back to data.message.
function renderWithIntl(node: React.ReactNode, locale = 'en') {
  return render(
    <NextIntlClientProvider locale={locale} messages={messages}>
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

  it('falls back to data.message when i18n key is missing (default provider)', () => {
    // The default NextIntlClientProvider returns the key string on missing keys.
    // RunErrorBubble detects this (localized === error_code) and uses data.message.
    renderWithIntl(
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

  it('falls back to data.message when key is missing and no params field', () => {
    // Same key-string detection path, no params provided.
    renderWithIntl(
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
