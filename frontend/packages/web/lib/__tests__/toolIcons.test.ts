import { describe, expect, it } from 'vitest'
import { getParamSummary } from '@/lib/toolIcons'

describe('getParamSummary', () => {
  it('prefers model-supplied description over command', () => {
    expect(
      getParamSummary('execute', {
        command: 'sleep 5 && echo done',
        description: 'Wait then print done',
      }),
    ).toBe('Wait then print done')
  })

  it('falls back to command for execute when description is empty', () => {
    expect(getParamSummary('execute', { command: 'ls -la', description: '  ' })).toBe('ls -la')
  })

  it('uses path for write when no description', () => {
    expect(getParamSummary('write', { file_path: 'src/main.ts' })).toBe('src/main.ts')
  })

  it('uses query for web_search', () => {
    expect(getParamSummary('web_search', { query: 'cubeplex agent' })).toBe('cubeplex agent')
  })

  it('truncates long descriptions', () => {
    const long = 'a'.repeat(80)
    const out = getParamSummary('execute', { description: long, command: 'x' })
    expect(out.endsWith('...')).toBe(true)
    expect(out.length).toBe(63)
  })

  it('collapses whitespace in description', () => {
    expect(getParamSummary('task', { description: 'Find  auth\nmiddleware' })).toBe(
      'Find auth middleware',
    )
  })
})
