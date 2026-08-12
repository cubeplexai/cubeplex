import { describe, expect, it } from 'vitest'
import { applySkillChipsToContent } from '../skillChips'

describe('applySkillChipsToContent', () => {
  it('returns content unchanged without chips', () => {
    expect(applySkillChipsToContent('hello', [])).toBe('hello')
  })

  it('prefixes a single skill with natural language', () => {
    expect(applySkillChipsToContent('compare Q4', [{ id: '1', name: 'deep-research' }])).toBe(
      'Use skill `deep-research`.\n\ncompare Q4',
    )
  })

  it('lists multiple skills', () => {
    expect(
      applySkillChipsToContent('go', [
        { id: '1', name: 'a' },
        { id: '2', name: 'org:b' },
      ]),
    ).toBe('Use skills `a`, `org:b`.\n\ngo')
  })

  it('sends prefix alone when the draft is empty', () => {
    expect(applySkillChipsToContent('  ', [{ id: '1', name: 'deep-research' }])).toBe(
      'Use skill `deep-research`.',
    )
  })
})
