'use client'

import { useCallback, useEffect, useRef } from 'react'
import { toast } from 'sonner'

const UNDO_WINDOW_MS = 5000

interface PendingDelete {
  timer: ReturnType<typeof setTimeout>
  commit: () => void | Promise<void>
  errorLabel: string
}

interface UndoableDeleteOpts {
  /** translated toast text, e.g. t('common.deleted') */
  label: string
  /** translated action text, e.g. t('common.undo') */
  actionLabel: string
  /** translated error text when commit fails after the window closes */
  errorLabel?: string
  onUndo?: () => void
}

// Module-level registry of pending deletes that survives component unmounts.
// Without this, a route change inside the 5s window would force-commit every
// pending delete (no UI left to undo it), which violates the toast contract.
// The keyed Map lets multiple hook instances coordinate safely.
const pending = new Map<string, PendingDelete>()

function safeCommit(entry: PendingDelete): void {
  Promise.resolve()
    .then(entry.commit)
    .catch((err) => {
      console.error('useUndoableDelete commit failed', err)
      toast.error(entry.errorLabel)
    })
}

/** Optimistic-hide + delayed-commit delete with an undo toast.
 *  Pending deletes outlive the calling component (route change does NOT
 *  force commit); the timer fires per its original schedule and any commit
 *  failure surfaces via toast.error. */
export function useUndoableDelete() {
  const ownedIds = useRef(new Set<string>())

  const undo = useCallback((id: string) => {
    const entry = pending.get(id)
    if (entry) {
      clearTimeout(entry.timer)
      pending.delete(id)
      ownedIds.current.delete(id)
    }
  }, [])

  const requestDelete = useCallback(
    (id: string, commit: () => void | Promise<void>, opts: UndoableDeleteOpts) => {
      const errorLabel = opts.errorLabel ?? opts.label
      // If a delete for this id is already pending, cancel its timer first
      // — otherwise the old timer would fire mid-window and force-commit
      // whatever entry is currently in the map (the new one), shortening
      // the new toast's undo window.
      const existing = pending.get(id)
      if (existing) clearTimeout(existing.timer)
      const entry: PendingDelete = {
        timer: setTimeout(() => {
          const e = pending.get(id)
          if (!e) return
          pending.delete(id)
          ownedIds.current.delete(id)
          safeCommit(e)
        }, UNDO_WINDOW_MS),
        commit,
        errorLabel,
      }
      pending.set(id, entry)
      ownedIds.current.add(id)
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

  // Cleanup on unmount: do NOT force-commit. The module-level pending Map
  // owns the timers; they fire on their original schedule even after this
  // hook instance is gone. ownedIds is just a debug aid for inspecting what
  // this caller put into the queue.
  useEffect(() => {
    const owned = ownedIds.current
    return () => {
      owned.clear()
    }
  }, [])

  return { requestDelete, undo }
}
