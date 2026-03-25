"use client"

import * as React from "react"

interface CollapsibleProps extends React.ComponentProps<"div"> {
  open?: boolean
  onOpenChange?: (open: boolean) => void
  defaultOpen?: boolean
}

function Collapsible({ open, onOpenChange, defaultOpen, ...props }: CollapsibleProps) {
  const [internalOpen, setInternalOpen] = React.useState(defaultOpen ?? false)
  const isOpen = open !== undefined ? open : internalOpen

  return (
    <div
      data-slot="collapsible"
      data-state={isOpen ? "open" : "closed"}
      {...props}
    />
  )
}

function CollapsibleTrigger({ onClick, ...props }: React.ComponentProps<"button">) {
  return (
    <button
      data-slot="collapsible-trigger"
      type="button"
      onClick={onClick}
      {...props}
    />
  )
}

function CollapsibleContent({ ...props }: React.ComponentProps<"div">) {
  return (
    <div data-slot="collapsible-content" {...props} />
  )
}

export { Collapsible, CollapsibleTrigger, CollapsibleContent }
