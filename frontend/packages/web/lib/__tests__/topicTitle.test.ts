import { describe, expect, it } from 'vitest'
import { topicDisplayTitle } from '../topicTitle'

describe('topicDisplayTitle', () => {
  it('returns title when non-empty', () => {
    expect(topicDisplayTitle('项目 Alpha', 'New Group Chat')).toBe('项目 Alpha')
  })

  it('falls back on empty / whitespace / null', () => {
    expect(topicDisplayTitle('', 'New Group Chat')).toBe('New Group Chat')
    expect(topicDisplayTitle('   ', 'New Group Chat')).toBe('New Group Chat')
    expect(topicDisplayTitle(null, 'New Group Chat')).toBe('New Group Chat')
    expect(topicDisplayTitle(undefined, 'New Group Chat')).toBe('New Group Chat')
  })
})
