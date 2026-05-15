import { describe, it, expect } from 'vitest'
import { bareToolName } from '../../src/lib/toolName'

describe('bareToolName', () => {
  it('strips the server namespace prefix', () => {
    expect(bareToolName('webtools__web_search')).toBe('web_search')
    expect(bareToolName('Cloudflare_Workers__fetch_url')).toBe('fetch_url')
  })

  it('returns bare names unchanged', () => {
    expect(bareToolName('web_search')).toBe('web_search')
    expect(bareToolName('execute')).toBe('execute')
  })

  it('handles multiple "__" by splitting on the FIRST occurrence', () => {
    // Edge: bare tool name itself contains "__" — unlikely in practice but stable
    expect(bareToolName('a__b__c')).toBe('b__c')
  })

  it('handles disambiguated namespaced names (server slug with suffix)', () => {
    expect(bareToolName('WebTools_aaaa__web_search')).toBe('web_search')
  })
})
