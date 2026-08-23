'use client'

import type { ReactNode, RefObject } from 'react'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { cn } from '@/lib/utils'

interface ArtifactExpandDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Artifact name for a11y labelling */
  title: string
  /** Stable identity so React remounts if selection swaps while open */
  identityKey: string
  /** Optional stable rail slot that permanently owns the preview dialog portal. */
  portalContainer?: HTMLElement
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
  portalContainer,
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
        portalContainer={portalContainer}
        portalClassName={portalContainer ? 'contents' : undefined}
        keepMounted={Boolean(portalContainer)}
        forceVisible={Boolean(portalContainer)}
        className={cn(
          'flex max-w-none flex-col gap-0 overflow-hidden p-0 sm:max-w-none',
          open
            ? 'fixed h-[90vh] w-[min(90vw,1400px)]'
            : cn(
                'absolute inset-0 z-0 h-full w-full translate-x-0 translate-y-0 rounded-none',
                'bg-background ring-0 duration-0 data-closed:animate-none',
              ),
        )}
        role={open ? 'dialog' : 'presentation'}
        aria-describedby={undefined}
        initialFocus={initialFocusRef}
        finalFocus={finalFocusRef}
      >
        {open && <DialogTitle className="sr-only">{title}</DialogTitle>}
        {open && header}
        <div
          className="min-h-0 flex-1 overflow-hidden"
          data-testid={open ? 'artifact-expand-preview' : 'artifact-rail-preview'}
        >
          {children}
        </div>
      </DialogContent>
    </Dialog>
  )
}
