'use client'

import type { ReactNode, RefObject } from 'react'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'

interface ArtifactExpandDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Artifact name for a11y labelling */
  title: string
  /** Stable identity so React remounts if selection swaps while open */
  identityKey: string
  header: ReactNode
  children: ReactNode
  /**
   * Prefer the Exit expand control so keyboard users start outside embedded
   * iframes (HTML/Office), where Esc would not reach the dialog.
   */
  initialFocusRef?: RefObject<HTMLElement | null>
  /** Restore focus to the rail Expand control when still mounted. */
  finalFocusRef?: RefObject<HTMLElement | null>
}

/**
 * In-app theater for artifact preview: large centered dialog (~90vw × 90vh).
 * Esc / backdrop / controlled onOpenChange(false) close expand only — callers
 * decide whether panelStore selection is kept.
 *
 * Modal by design: the rail under the backdrop is inert while open. Exit expand
 * first, then use the rail Close control to dismiss the whole panel.
 */
export function ArtifactExpandDialog({
  open,
  onOpenChange,
  title,
  identityKey,
  header,
  children,
  initialFocusRef,
  finalFocusRef,
}: ArtifactExpandDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        key={identityKey}
        showCloseButton={false}
        className="flex h-[90vh] w-[min(90vw,1400px)] max-w-none flex-col gap-0 overflow-hidden
          p-0 sm:max-w-none"
        aria-describedby={undefined}
        initialFocus={initialFocusRef}
        finalFocus={finalFocusRef}
      >
        <DialogTitle className="sr-only">{title}</DialogTitle>
        {header}
        <div className="min-h-0 flex-1 overflow-hidden" data-testid="artifact-expand-preview">
          {children}
        </div>
      </DialogContent>
    </Dialog>
  )
}
