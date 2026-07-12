'use client'

import { useToolDetailStore } from '@cubeplex/core'

export function useToolDetail() {
  const isOpen = useToolDetailStore((s) => s.isOpen)
  const toolName = useToolDetailStore((s) => s.toolName)
  const toolArgs = useToolDetailStore((s) => s.toolArgs)
  const toolResult = useToolDetailStore((s) => s.toolResult)
  const contentType = useToolDetailStore((s) => s.contentType)
  const toolRef = useToolDetailStore((s) => s.toolRef)
  const highlightText = useToolDetailStore((s) => s.highlightText)
  const highlightKey = useToolDetailStore((s) => s.highlightKey)
  const open = useToolDetailStore((s) => s.open)
  const close = useToolDetailStore((s) => s.close)

  return {
    isOpen,
    toolName,
    toolArgs,
    toolResult,
    contentType,
    toolRef,
    highlightText,
    highlightKey,
    open,
    close,
  }
}
