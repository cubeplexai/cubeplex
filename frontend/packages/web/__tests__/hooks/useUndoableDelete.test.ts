import { renderHook, act } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useUndoableDelete } from '@/hooks/useUndoableDelete'

const OPTS = { label: 'Deleted', actionLabel: 'Undo' }

describe('useUndoableDelete', () => {
  it('commits after the grace window unless undone', () => {
    vi.useFakeTimers()
    const commit = vi.fn()
    const { result } = renderHook(() => useUndoableDelete())
    act(() => result.current.requestDelete('item-1', commit, OPTS))
    expect(commit).not.toHaveBeenCalled() // delayed
    act(() => vi.advanceTimersByTime(5000))
    expect(commit).toHaveBeenCalledTimes(1) // committed
    vi.useRealTimers()
  })

  it('does not commit when undone within the window', () => {
    vi.useFakeTimers()
    const commit = vi.fn()
    const { result } = renderHook(() => useUndoableDelete())
    act(() => result.current.requestDelete('item-2', commit, OPTS))
    act(() => result.current.undo('item-2'))
    act(() => vi.advanceTimersByTime(5000))
    expect(commit).not.toHaveBeenCalled()
    vi.useRealTimers()
  })

  it('FLUSHES (commits) pending deletes on unmount — never cancels them', () => {
    vi.useFakeTimers()
    const commit = vi.fn()
    const { result, unmount } = renderHook(() => useUndoableDelete())
    act(() => result.current.requestDelete('item-3', commit, OPTS))
    unmount() // user navigated away inside the grace window
    expect(commit).toHaveBeenCalledTimes(1) // the delete the toast promised still happens
    vi.useRealTimers()
  })
})
