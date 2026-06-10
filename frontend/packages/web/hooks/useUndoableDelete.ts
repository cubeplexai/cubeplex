'use client'

import { useCallback, useEffect, useRef } from 'react'
import { toast } from 'sonner'

const UNDO_WINDOW_MS = 5000

interface PendingDelete {
  timer: ReturnType<typeof setTimeout>
  commit: () => void | Promise<void>
}

interface UndoableDeleteOpts {
  /** translated toast text, e.g. t('common.deleted') */
  label: string
  /** translated action text, e.g. t('common.undo') */
  actionLabel: string
  onUndo?: () => void
}

/** Optimistic-hide + delayed-commit delete with an undo toast.
 *  Unmount FLUSHES pending commits (the UI already promised deletion). */
export function useUndoableDelete() {
  const pending = useRef(new Map<string, PendingDelete>())

  const undo = useCallback((id: string) => {
    const entry = pending.current.get(id)
    if (entry) {
      clearTimeout(entry.timer)
      pending.current.delete(id)
    }
  }, [])

  const requestDelete = useCallback(
    (id: string, commit: () => void | Promise<void>, opts: UndoableDeleteOpts) => {
      const timer = setTimeout(() => {
        pending.current.delete(id)
        void commit()
      }, UNDO_WINDOW_MS)
      pending.current.set(id, { timer, commit })
      toast(opts.label, {
        duration: UNDO_WINDOW_MS,
        action: {
          label: opts.actionLabel,
          onClick: () => {
            undo(id)
            opts.onUndo?.()
          },
        },
      })
    },
    [undo],
  )

  useEffect(() => {
    const map = pending.current
    return () => {
      // flush, don't cancel: commit everything still pending
      for (const { timer, commit } of map.values()) {
        clearTimeout(timer)
        void commit()
      }
      map.clear()
    }
  }, [])

  return { requestDelete, undo }
}
