"use client"

import { GripVertical } from "lucide-react"
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
        "bg-border focus-visible:ring-ring relative",
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
          className="bg-border z-10 flex h-4 w-3
            items-center justify-center rounded-xs
            border"
        >
          <GripVertical className="size-2.5" />
        </div>
      )}
    </Separator>
  )
}

export {
  ResizablePanelGroup,
  ResizablePanel,
  ResizableHandle,
}
