import { renderHook, act } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useUndoableDelete } from '@/hooks/useUndoableDelete'

const OPTS = { label: 'Deleted', actionLabel: 'Undo', errorLabel: 'Delete failed' }

describe('useUndoableDelete', () => {
  it('commits after the grace window unless undone', async () => {
    vi.useFakeTimers()
    const commit = vi.fn().mockResolvedValue(undefined)
    const { result } = renderHook(() => useUndoableDelete())
    act(() => result.current.requestDelete('item-1', commit, OPTS))
    expect(commit).not.toHaveBeenCalled()
    act(() => {
      vi.advanceTimersByTime(5000)
    })
    await act(async () => {
      await Promise.resolve()
    })
    expect(commit).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
  })

  it('does not commit when undone within the window', () => {
    vi.useFakeTimers()
    const commit = vi.fn()
    const { result } = renderHook(() => useUndoableDelete())
    act(() => result.current.requestDelete('item-2', commit, OPTS))
    act(() => result.current.undo('item-2'))
    act(() => {
      vi.advanceTimersByTime(5000)
    })
    expect(commit).not.toHaveBeenCalled()
    vi.useRealTimers()
  })

  it('does NOT force-commit on unmount: timers fire on their original schedule', async () => {
    vi.useFakeTimers()
    const commit = vi.fn().mockResolvedValue(undefined)
    const { result, unmount } = renderHook(() => useUndoableDelete())
    act(() => result.current.requestDelete('item-3', commit, OPTS))
    unmount()
    expect(commit).not.toHaveBeenCalled()
    act(() => {
      vi.advanceTimersByTime(5000)
    })
    await act(async () => {
      await Promise.resolve()
    })
    expect(commit).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
  })
})
