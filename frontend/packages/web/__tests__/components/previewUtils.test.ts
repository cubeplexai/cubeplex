import { describe, it, expect } from 'vitest'
import { hasImageExt } from '../../components/panel/artifact/previewUtils'

describe('hasImageExt', () => {
  it('true for image extensions (case-insensitive)', () => {
    expect(hasImageExt('1_镇街贷款金额.png')).toBe(true)
    expect(hasImageExt('chart.JPG')).toBe(true)
    expect(hasImageExt('a.svg')).toBe(true)
    expect(hasImageExt('a.webp')).toBe(true)
  })

  it('false for non-image extensions and extensionless names', () => {
    expect(hasImageExt('charts')).toBe(false)
    expect(hasImageExt('script.py')).toBe(false)
    expect(hasImageExt('data.csv')).toBe(false)
    expect(hasImageExt('')).toBe(false)
  })
})
