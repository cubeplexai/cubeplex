import { describe, expect, it } from 'vitest'
import { htmlIsDark, remapOperatorTheme, resolveColorMode } from './themeFlavor'

describe('resolveColorMode', () => {
  it('reads default-family names', () => {
    expect(resolveColorMode('light')).toBe('light')
    expect(resolveColorMode('dark')).toBe('dark')
  })

  it('reads operator-family names as the same color mode', () => {
    expect(resolveColorMode('operator-light')).toBe('light')
    expect(resolveColorMode('operator-dark')).toBe('dark')
  })

  it('falls back to resolvedTheme when theme is system', () => {
    expect(resolveColorMode('system', 'dark')).toBe('dark')
    expect(resolveColorMode('system', 'light')).toBe('light')
    expect(resolveColorMode('system', 'operator-dark')).toBe('dark')
  })
})

describe('remapOperatorTheme', () => {
  it('maps operator names onto the default family', () => {
    expect(remapOperatorTheme('operator-light')).toBe('light')
    expect(remapOperatorTheme('operator-dark')).toBe('dark')
  })

  it('leaves default-family and system values alone', () => {
    expect(remapOperatorTheme('light')).toBeNull()
    expect(remapOperatorTheme('dark')).toBeNull()
    expect(remapOperatorTheme('system')).toBeNull()
    expect(remapOperatorTheme(undefined)).toBeNull()
  })
})

describe('htmlIsDark', () => {
  const classListOf = (...tokens: string[]) => ({
    contains: (name: string) => tokens.includes(name),
  })

  it('treats .dark and .operator-dark as dark', () => {
    expect(htmlIsDark({ classList: classListOf('dark') })).toBe(true)
    expect(htmlIsDark({ classList: classListOf('operator-dark') })).toBe(true)
  })

  it('treats default and operator light as not dark', () => {
    expect(htmlIsDark({ classList: classListOf() })).toBe(false)
    expect(htmlIsDark({ classList: classListOf('light') })).toBe(false)
    expect(htmlIsDark({ classList: classListOf('operator-light') })).toBe(false)
  })
})
