import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ThemeProvider, useTheme } from 'next-themes'
import { NextIntlClientProvider } from 'next-intl'
import { ThemeToggle } from '@/components/ui/theme-toggle'
import { DefaultThemeGuard } from '@/components/ui/default-theme-guard'
import { afterEach, describe, expect, it, vi } from 'vitest'

const intl = {
  locale: 'en' as const,
  messages: { avatar: { lightTheme: 'Light', darkTheme: 'Dark' } },
}

function stubMatchMedia(matchesDark: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: matchesDark && q.includes('dark'),
    media: q,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
    onchange: null,
  }))
}

function ThemeProbe() {
  const { theme } = useTheme()
  return <span data-testid="theme">{theme}</span>
}

function cleanupTheme() {
  vi.unstubAllGlobals()
  localStorage.clear()
  document.documentElement.className = ''
}

// system resolves dark; first click must flip to LIGHT (uses resolvedTheme)
describe('ThemeToggle under theme=system', () => {
  afterEach(cleanupTheme)

  it('first click flips against resolvedTheme, not raw theme', () => {
    stubMatchMedia(true)
    render(
      <NextIntlClientProvider {...intl}>
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          <ThemeToggle />
        </ThemeProvider>
      </NextIntlClientProvider>,
    )
    fireEvent.click(screen.getByRole('button'))
    expect(document.documentElement.classList.contains('light')).toBe(true)
  })

  it('writes default-family names even when operator is active', async () => {
    stubMatchMedia(false)
    render(
      <NextIntlClientProvider {...intl}>
        <ThemeProvider
          attribute="class"
          defaultTheme="operator-dark"
          enableSystem={false}
          themes={['light', 'dark', 'operator-light', 'operator-dark']}
        >
          <ThemeToggle />
        </ThemeProvider>
      </NextIntlClientProvider>,
    )
    fireEvent.click(await screen.findByRole('button'))
    expect(document.documentElement.classList.contains('light')).toBe(true)
    expect(document.documentElement.classList.contains('operator-light')).toBe(false)
    expect(document.documentElement.classList.contains('operator-dark')).toBe(false)
  })
})

describe('DefaultThemeGuard', () => {
  afterEach(cleanupTheme)

  it('remaps a leftover operator-dark value onto dark', async () => {
    stubMatchMedia(false)
    render(
      <ThemeProvider
        attribute="class"
        defaultTheme="operator-dark"
        enableSystem={false}
        themes={['light', 'dark', 'operator-light', 'operator-dark']}
      >
        <DefaultThemeGuard />
        <ThemeProbe />
      </ThemeProvider>,
    )
    await waitFor(() => {
      expect(screen.getByTestId('theme').textContent).toBe('dark')
    })
  })
})
