const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/

export type SlugError = 'slug_too_short' | 'slug_invalid_format'

export const SLUG_MIN = 3
export const SLUG_MAX = 32

export function validateSlug(slug: string): SlugError | null {
  if (slug.length < SLUG_MIN) return 'slug_too_short'
  if (!SLUG_RE.test(slug)) return 'slug_invalid_format'
  return null
}

export function slugErrorMessage(code: SlugError | 'slug_taken'): string {
  switch (code) {
    case 'slug_too_short':
      return 'Must be at least 3 characters.'
    case 'slug_invalid_format':
      return 'Use only lowercase letters, digits, and hyphens; must start and end with a letter or digit.'
    case 'slug_taken':
      return 'That identifier is already in use.'
  }
}

export function suggestSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, SLUG_MAX)
}
