import { render, screen, fireEvent } from '@testing-library/react'
import { ThemeProvider } from 'next-themes'
import { NextIntlClientProvider } from 'next-intl'
import { ThemeToggle } from '@/components/ui/theme-toggle'
import { afterEach, describe, expect, it, vi } from 'vitest'

// system resolves dark; first click must flip to LIGHT (uses resolvedTheme)
describe('ThemeToggle under theme=system', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('first click flips against resolvedTheme, not raw theme', () => {
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: q.includes('dark'),
      media: q,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
      onchange: null,
    }))
    render(
      <NextIntlClientProvider
        locale="en"
        messages={{ avatar: { lightTheme: 'Light', darkTheme: 'Dark' } }}
      >
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          <ThemeToggle />
        </ThemeProvider>
      </NextIntlClientProvider>,
    )
    fireEvent.click(screen.getByRole('button'))
    expect(document.documentElement.classList.contains('light')).toBe(true)
  })
})
