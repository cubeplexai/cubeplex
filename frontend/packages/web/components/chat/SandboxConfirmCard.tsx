'use client'

import { useState, useEffect } from 'react'
import { Check, X, Clock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { PendingConfirm } from '@cubeplex/core'

interface SandboxConfirmCardProps {
  pending: PendingConfirm
  onApprove: () => Promise<void>
  onDeny: () => Promise<void>
}

export function SandboxConfirmCard({ pending, onApprove, onDeny }: SandboxConfirmCardProps) {
  const [submitting, setSubmitting] = useState<'approve' | 'deny' | null>(null)
  const [secondsLeft, setSecondsLeft] = useState<number | null>(() => {
    if (pending.timeout_seconds === null) return null
    const elapsed = Math.floor((Date.now() - pending.requestedAt) / 1000)
    return Math.max(0, pending.timeout_seconds - elapsed)
  })

  useEffect(() => {
    if (secondsLeft === null || secondsLeft <= 0) return
    const id = setInterval(() => setSecondsLeft((s) => (s !== null && s > 0 ? s - 1 : 0)), 1000)
    return () => clearInterval(id)
  }, [secondsLeft])

  const handle = async (decision: 'approve' | 'deny') => {
    if (submitting) return
    setSubmitting(decision)
    try {
      if (decision === 'approve') await onApprove()
      else await onDeny()
    } catch {
      setSubmitting(null)
    }
  }

  return (
    <div className="my-2 rounded-lg border border-warning-border bg-warning-surface p-3">
      <div className="mb-2 flex items-center gap-2 text-sm font-medium text-warning-fg">
        <Clock className="h-4 w-4 shrink-0" />
        <span>Command requires approval</span>
        {secondsLeft !== null && secondsLeft > 0 && (
          <span className="ml-auto tabular-nums text-warning-fg">{secondsLeft}s</span>
        )}
      </div>
      <code className="mb-3 block rounded bg-sunken px-2 py-1 text-xs text-warning-fg">
        {pending.command}
      </code>
      {pending.matched_pattern && (
        <p className="mb-3 text-xs text-warning-fg">
          Matched rule: <code className="font-mono">{pending.matched_pattern}</code>
        </p>
      )}
      <div className="flex gap-2">
        <Button
          size="sm"
          className="gap-1 bg-success-solid hover:bg-success-solid/90 text-primary-foreground"
          disabled={!!submitting}
          onClick={() => handle('approve')}
        >
          <Check className="h-3.5 w-3.5" />
          {submitting === 'approve' ? 'Approving…' : 'Approve'}
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="gap-1 border-danger-border text-danger-fg hover:bg-danger-surface"
          disabled={!!submitting}
          onClick={() => handle('deny')}
        >
          <X className="h-3.5 w-3.5" />
          {submitting === 'deny' ? 'Denying…' : 'Deny'}
        </Button>
      </div>
    </div>
  )
}
