/**
 * Pure helper functions for avatar generation.
 *
 * - `initials(name)` — first-letter of first + last word, uppercased.
 * - `avatarColor(seed)` — deterministic pastel hex colour from any seed.
 * - `randomSeed()` — crypto-random string for ad-hoc avatars.
 * - `svgToPngBlob(svg, size)` — renders an SVG string to a PNG Blob via
 *   Canvas 2D (DOM API, only available in browser / Playwright env).
 */

const TRIM = /^\s+|\s+$/g
const SPACES = /\s+/g

/**
 * Extract initials from a name string.
 *
 * Takes the first character of the first word and the first character of the
 * last word, uppercased.  A single word yields one initial; empty / nullish
 * input yields `""`.
 */
export function initials(name: string | null | undefined): string {
  if (!name) return ''
  const parts = name.replace(TRIM, '').replace(SPACES, ' ').split(' ')
  const first = parts[0]?.[0] ?? ''
  const last = parts.length > 1 ? parts[parts.length - 1][0] : ''
  return (first + last).toUpperCase()
}

/**
 * Deterministic pastel colour from an arbitrary seed.
 *
 * The seed is hashed via DJB2, then the first 3 bytes of the hash are used as
 * R/G/B values, blended toward 0xCC (soft pastel).  Returns a hex string
 * suitable for use as a CSS colour.
 */
export function avatarColor(seed: string | number | null | undefined): string {
  const s = String(seed ?? '')
  let hash = 5381
  for (let i = 0; i < s.length; i++) {
    hash = ((hash << 5) + hash + s.charCodeAt(i)) & 0xffffffff
  }
  const r = ((hash & 0xff) + 0xcc) >> 1
  const g = (((hash >> 8) & 0xff) + 0xcc) >> 1
  const b = (((hash >> 16) & 0xff) + 0xcc) >> 1
  return `#${[r, g, b].map((c) => c.toString(16).padStart(2, '0')).join('')}`
}

/**
 * Generate a cryptographically random seed string.
 */
export function randomSeed(): string {
  const bytes = new Uint8Array(8)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

/**
 * Render an SVG string to a PNG Blob using Canvas 2D.
 *
 * Requires DOM APIs (Image, Canvas).  In Node / Vitest this throws; it is
 * intended for Playwright tests and browser runtime only.
 *
 * @param svg  — SVG markup string.
 * @param size — output dimensions (square).
 */
export async function svgToPngBlob(svg: string, size: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    const canvas = document.createElement('canvas')
    canvas.width = size
    canvas.height = size
    const ctx = canvas.getContext('2d')!

    const blob = () => {
      canvas.toBlob((b) => {
        b ? resolve(b) : reject(new Error('canvas.toBlob returned null'))
      }, 'image/png')
    }

    img.onload = () => {
      ctx.clearRect(0, 0, size, size)
      ctx.drawImage(img, 0, 0, size, size)
      blob()
    }
    img.onerror = () => reject(new Error('Failed to load SVG image'))

    img.src = `data:image/svg+xml;base64,${btoa(svg)}`
  })
}
