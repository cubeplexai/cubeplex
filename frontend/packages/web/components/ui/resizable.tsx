"use client"

import {
  Group,
  Panel,
  Separator,
} from "react-resizable-panels"
import { cn } from "@/lib/utils"

function ResizablePanelGroup({
  className,
  ...props
}: React.ComponentProps<typeof Group>) {
  return (
    <Group
      data-slot="resizable-panel-group"
      className={cn("flex h-full w-full", className)}
      {...props}
    />
  )
}

function ResizablePanel({
  ...props
}: React.ComponentProps<typeof Panel>) {
  return (
    <Panel
      data-slot="resizable-panel"
      {...props}
    />
  )
}

function ResizableHandle({
  withHandle,
  className,
  ...props
}: React.ComponentProps<typeof Separator> & {
  withHandle?: boolean
}) {
  return (
    <Separator
      data-slot="resizable-handle"
      className={cn(
        "group bg-border focus-visible:ring-ring relative",
        "flex w-px items-center justify-center",
        "after:absolute after:inset-y-0",
        "after:-left-2 after:-right-2",
        "focus-visible:ring-1",
        "focus-visible:ring-offset-1",
        "focus-visible:outline-hidden",
        "data-[panel-group-direction=vertical]:h-px",
        "data-[panel-group-direction=vertical]:w-full",
        "data-[panel-group-direction=vertical]:after:left-0",
        "data-[panel-group-direction=vertical]:after:h-4",
        "data-[panel-group-direction=vertical]:after:-top-2",
        "data-[panel-group-direction=vertical]:after:-bottom-2",
        "data-[panel-group-direction=vertical]:after:w-full",
        "[&[data-panel-group-direction=vertical]>div]:rotate-90",
        className,
      )}
      {...props}
    >
      {withHandle && (
        <div
          className="z-10 flex h-8 w-1 items-center justify-center
            rounded-full bg-border transition-all duration-150
            group-hover:h-12 group-hover:w-1.5
            group-hover:bg-muted-foreground/40
            group-active:h-16 group-active:bg-primary/50"
        />
      )}
    </Separator>
  )
}

export {
  ResizablePanelGroup,
  ResizablePanel,
  ResizableHandle,
}
