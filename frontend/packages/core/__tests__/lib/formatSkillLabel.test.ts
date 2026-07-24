import { describe, it, expect } from 'vitest'
import { formatSkillLabel } from '../../src/lib/formatSkillLabel'

describe('formatSkillLabel', () => {
  it('extracts bare slug after org prefix', () => {
    expect(formatSkillLabel('acme-corp:quarterly-report')).toEqual({
      primary: 'quarterly-report',
      canonical: 'acme-corp:quarterly-report',
      isNamespaced: true,
      namespace: 'acme-corp',
    })
  })

  it('leaves preinstalled bare names unchanged', () => {
    expect(formatSkillLabel('deep-research')).toEqual({
      primary: 'deep-research',
      canonical: 'deep-research',
      isNamespaced: false,
      namespace: null,
    })
  })

  it('uses the segment after the last colon for multi-colon names', () => {
    expect(formatSkillLabel('a:b:c')).toEqual({
      primary: 'c',
      canonical: 'a:b:c',
      isNamespaced: true,
      namespace: 'a:b',
    })
  })

  it('falls back safely on empty string', () => {
    expect(formatSkillLabel('')).toEqual({
      primary: '',
      canonical: '',
      isNamespaced: false,
      namespace: null,
    })
  })

  it('falls back to canonical when primary after colon is empty', () => {
    expect(formatSkillLabel('org:')).toEqual({
      primary: 'org:',
      canonical: 'org:',
      isNamespaced: true,
      namespace: 'org',
    })
  })
})
