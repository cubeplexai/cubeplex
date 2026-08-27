/** Color mode independent of theme family (default vs operator). */

export type ColorMode = 'light' | 'dark'

export function isLightThemeName(name: string | undefined): boolean {
  return name === 'light' || name === 'operator-light'
}

export function isDarkThemeName(name: string | undefined): boolean {
  return name === 'dark' || name === 'operator-dark'
}

export function resolveColorMode(theme: string | undefined, resolvedTheme?: string): ColorMode {
  if (isLightThemeName(theme)) return 'light'
  if (isDarkThemeName(theme)) return 'dark'
  if (isLightThemeName(resolvedTheme)) return 'light'
  if (isDarkThemeName(resolvedTheme)) return 'dark'
  return 'light'
}

/** Map a leftover operator-* next-themes value onto the default family. */
export function remapOperatorTheme(theme: string | undefined): ColorMode | null {
  if (theme === 'operator-light') return 'light'
  if (theme === 'operator-dark') return 'dark'
  return null
}

export function htmlIsDark(html: { classList: { contains(token: string): boolean } }): boolean {
  return html.classList.contains('dark') || html.classList.contains('operator-dark')
}
