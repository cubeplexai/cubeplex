import { describe, expect, it } from 'vitest'
import { getEditSpecs } from './EditFilePreviewView'

describe('getEditSpecs', () => {
  it('returns every valid edit from the batch contract', () => {
    expect(
      getEditSpecs({
        edits: [
          { old_string: 'first', new_string: 'one' },
          { old_string: 'second', new_string: 'two' },
          { old_string: 3, new_string: 'ignored' },
        ],
      }),
    ).toEqual([
      { old_string: 'first', new_string: 'one' },
      { old_string: 'second', new_string: 'two' },
    ])
  })

  it('normalizes the legacy single-edit shape', () => {
    expect(getEditSpecs({ old_string: 'before', new_string: 'after' })).toEqual([
      { old_string: 'before', new_string: 'after' },
    ])
  })
})
