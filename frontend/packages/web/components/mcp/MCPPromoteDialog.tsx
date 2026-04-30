'use client'

import { useEffect, useState } from 'react'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import type { MCPServer } from '@cubebox/core'
import { Loader2, X } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { cn } from '@/lib/utils'

export interface MCPPromoteDialogProps {
  server: MCPServer
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: (shareCredential: boolean) => Promise<void>
}

export function MCPPromoteDialog({ server, open, onOpenChange, onConfirm }: MCPPromoteDialogProps) {
  const canShareCredential = server.credential_scope === 'workspace'
  const [shareCredential, setShareCredential] = useState(canShareCredential)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setShareCredential(canShareCredential)
      setError(null)
    }
  }, [canShareCredential, open])

  async function handleConfirm(): Promise<void> {
    setSubmitting(true)
    setError(null)
    try {
      await onConfirm(canShareCredential ? shareCredential : false)
      onOpenChange(false)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200" />
        <DialogPrimitive.Popup
          className={cn(
            'fixed left-1/2 top-1/2 z-50 w-[min(520px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2',
            'rounded-xl border border-border bg-popover p-5 text-popover-foreground shadow-2xl',
            'data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200',
          )}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="flex flex-col gap-1">
              <DialogPrimitive.Title className="text-base font-semibold">
                Promote MCP server
              </DialogPrimitive.Title>
              <DialogPrimitive.Description className="text-sm text-muted-foreground">
                Move {server.name} to organization scope so admins can bind it to workspaces.
              </DialogPrimitive.Description>
            </div>
            <DialogPrimitive.Close
              render={
                <button
                  type="button"
                  aria-label="Close promote dialog"
                  className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                  disabled={submitting}
                >
                  <X />
                </button>
              }
            />
          </div>

          <div className="mt-4 flex flex-col gap-4">
            {error && (
              <Alert variant="destructive">
                <AlertTitle>Promotion failed</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            {canShareCredential ? (
              <RadioGroup
                value={shareCredential ? 'share' : 'keep-workspace'}
                onValueChange={(value) => setShareCredential(value === 'share')}
              >
                <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-border p-3">
                  <RadioGroupItem value="share" disabled={submitting} />
                  <span className="flex flex-col gap-1">
                    <span className="text-sm font-medium">Share credential with organization</span>
                    <span className="text-sm text-muted-foreground">
                      Convert the workspace credential into an organization credential and attach it
                      to the promoted server.
                    </span>
                  </span>
                </label>
                <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-border p-3">
                  <RadioGroupItem value="keep-workspace" disabled={submitting} />
                  <span className="flex flex-col gap-1">
                    <span className="text-sm font-medium">Promote without credential</span>
                    <span className="text-sm text-muted-foreground">
                      Keep credentials workspace-local. Admins can add an organization credential
                      later.
                    </span>
                  </span>
                </label>
              </RadioGroup>
            ) : (
              <p className="rounded-lg border border-border bg-muted/30 p-3 text-sm text-muted-foreground">
                This server has no workspace credential to share. It will be promoted without an
                organization credential.
              </p>
            )}

            <div className="flex justify-end gap-2">
              <DialogPrimitive.Close
                render={
                  <Button type="button" variant="ghost" disabled={submitting}>
                    Cancel
                  </Button>
                }
              />
              <Button type="button" disabled={submitting} onClick={() => void handleConfirm()}>
                {submitting && <Loader2 data-icon="inline-start" className="animate-spin" />}
                Promote server
              </Button>
            </div>
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
