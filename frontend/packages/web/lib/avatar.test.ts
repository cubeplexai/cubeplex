import { describe, it, expect } from 'vitest'
import { initials, avatarColor, randomSeed } from './avatar'

describe('initials', () => {
  it('extracts two initials from a full name', () => {
    expect(initials('Alice Bob')).toBe('AB')
  })

  it('returns a single initial for a single name', () => {
    expect(initials('Alice')).toBe('A')
  })

  it('handles multi-word names — first and last only', () => {
    expect(initials('Alice Bob Charlie')).toBe('AC')
  })

  it('handles lowercase input', () => {
    expect(initials('alice bob')).toBe('AB')
  })

  it('handles empty string', () => {
    expect(initials('')).toBe('')
  })

  it('handles whitespace-only string', () => {
    expect(initials('   ')).toBe('')
  })

  it('collapses multiple spaces', () => {
    expect(initials('alice   bob')).toBe('AB')
  })

  it('ignores leading/trailing whitespace', () => {
    expect(initials('  alice bob  ')).toBe('AB')
  })

  it('returns empty string for undefined', () => {
    expect(initials(undefined)).toBe('')
  })

  it('returns empty string for null', () => {
    expect(initials(null)).toBe('')
  })
})

describe('avatarColor', () => {
  it("returns a string of length 7 (e.g. '#a1b2c3')", () => {
    const color = avatarColor('hello')
    expect(color).toMatch(/^#[0-9a-f]{6}$/)
  })

  it('returns the same color for the same seed', () => {
    expect(avatarColor('hello')).toBe(avatarColor('hello'))
  })

  it('returns different colors for different seeds', () => {
    expect(avatarColor('hello')).not.toBe(avatarColor('world'))
  })

  it('handles empty seed', () => {
    const color = avatarColor('')
    expect(color).toMatch(/^#[0-9a-f]{6}$/)
  })

  it('handles numeric seed', () => {
    const color = avatarColor(42)
    expect(color).toMatch(/^#[0-9a-f]{6}$/)
  })

  it('handles null seed', () => {
    const color = avatarColor(null)
    expect(color).toMatch(/^#[0-9a-f]{6}$/)
  })

  it('handles undefined seed', () => {
    const color = avatarColor(undefined)
    expect(color).toMatch(/^#[0-9a-f]{6}$/)
  })
})

describe('randomSeed', () => {
  it('returns a string', () => {
    expect(typeof randomSeed()).toBe('string')
  })

  it('returns different values on successive calls', () => {
    expect(randomSeed()).not.toBe(randomSeed())
  })

  it('returns a non-empty string', () => {
    expect(randomSeed().length).toBeGreaterThan(0)
  })
})
