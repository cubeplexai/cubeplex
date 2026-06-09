import { describe, expect, it } from 'vitest'

import { reorder } from '@/app/admin/presets/PresetEditor'

// `reorder` is the pure array helper used by both the arrow buttons and
// the HTML5 drag-drop reorder handler. The semantics that matter:
//   - `to` is the index the moved item must occupy in the final array.
//   - Arrow buttons call reorder(arr, idx, idx ± 1) — they expect a
//     simple swap with the neighbour.
//   - Drag-drop calls reorder(arr, draggingIdx, dropTargetIdx) — they
//     expect the dragged item to take the drop target's slot.
// Both shapes are exercised below so a future "off-by-one fix" can't
// silently break either site.

describe('reorder', () => {
  it('moves an item down by one (arrow "move down" semantics)', () => {
    // B should swap with C → [A, C, B, D]
    expect(reorder(['A', 'B', 'C', 'D'], 1, 2)).toEqual(['A', 'C', 'B', 'D'])
  })

  it('moves an item up by one (arrow "move up" semantics)', () => {
    // C should swap with B → [A, C, B, D]
    expect(reorder(['A', 'B', 'C', 'D'], 2, 1)).toEqual(['A', 'C', 'B', 'D'])
  })

  it('moves an item several slots down (drag onto distant target)', () => {
    // Drag A (0) onto C (2): A lands at C's original slot; C shifts up.
    expect(reorder(['A', 'B', 'C', 'D'], 0, 2)).toEqual(['B', 'C', 'A', 'D'])
  })

  it('moves an item several slots up (drag onto distant target)', () => {
    // Drag D (3) onto B (1): D lands at B's original slot; B shifts down.
    expect(reorder(['A', 'B', 'C', 'D'], 3, 1)).toEqual(['A', 'D', 'B', 'C'])
  })

  it('is a no-op when from === to', () => {
    expect(reorder(['A', 'B', 'C'], 1, 1)).toEqual(['A', 'B', 'C'])
  })

  it('returns a copy when indices are out of range', () => {
    const src = ['A', 'B']
    const result = reorder(src, -1, 0)
    expect(result).toEqual(['A', 'B'])
    expect(result).not.toBe(src)
  })

  it('does not mutate the input array', () => {
    const src = ['A', 'B', 'C']
    const result = reorder(src, 0, 2)
    expect(src).toEqual(['A', 'B', 'C'])
    expect(result).toEqual(['B', 'C', 'A'])
  })
})
