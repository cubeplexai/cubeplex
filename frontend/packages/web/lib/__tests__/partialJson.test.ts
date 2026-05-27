import { describe, it, expect } from 'vitest'
import { extractJsonStringPrefix, extractWidgetCode } from '@/lib/partialJson'

describe('extractJsonStringPrefix', () => {
  it('extracts a complete value', () => {
    expect(extractJsonStringPrefix('{"content":"hello"}', 'content')).toBe('hello')
  })
  it('returns the prefix for an unterminated value', () => {
    expect(extractJsonStringPrefix('{"content":"# Title\\nbody', 'content')).toBe('# Title\nbody')
  })
  it('decodes escapes', () => {
    expect(extractJsonStringPrefix('{"c":"a\\tb\\"c\\u0041"}', 'c')).toBe('a\tb"cA')
  })
  it('does not end early on an escaped quote inside the value', () => {
    expect(extractJsonStringPrefix('{"c":"say \\"hi\\" ok"}', 'c')).toBe('say "hi" ok')
  })
  it('returns empty when the key is absent', () => {
    expect(extractJsonStringPrefix('{"other":"x"}', 'content')).toBe('')
  })
})

describe('extractWidgetCode', () => {
  it('pulls widget_code mid-stream', () => {
    expect(extractWidgetCode('{"title":"t","widget_code":"<div>part')).toBe('<div>part')
  })
})
