import { describe, expect, it } from 'vitest'
import { parseLeadingCommandToken } from '../parse'

describe('parseLeadingCommandToken', () => {
  it('returns null for empty or plain text', () => {
    expect(parseLeadingCommandToken('')).toBeNull()
    expect(parseLeadingCommandToken('hello')).toBeNull()
    expect(parseLeadingCommandToken('hello /mod')).toBeNull()
  })

  it('opens on bare slash and captures empty query', () => {
    expect(parseLeadingCommandToken('/')).toEqual({
      kind: 'command',
      raw: '/',
      query: '',
    })
  })

  it('captures query after slash', () => {
    expect(parseLeadingCommandToken('/mod')).toEqual({
      kind: 'command',
      raw: '/mod',
      query: 'mod',
    })
    expect(parseLeadingCommandToken('  /stop')).toEqual({
      kind: 'command',
      raw: '  /stop',
      query: 'stop',
    })
  })

  it('closes when space or newline ends the token', () => {
    expect(parseLeadingCommandToken('/foo bar')).toBeNull()
    expect(parseLeadingCommandToken('/model\nextra')).toBeNull()
  })
})
