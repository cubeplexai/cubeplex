'use client'

import { useToolDetailStore } from '@cubebox/core'

export function useToolDetail() {
  const isOpen = useToolDetailStore((s) => s.isOpen)
  const toolName =
    useToolDetailStore((s) => s.toolName)
  const toolArgs =
    useToolDetailStore((s) => s.toolArgs)
  const toolResult =
    useToolDetailStore((s) => s.toolResult)
  const contentType =
    useToolDetailStore((s) => s.contentType)
  const open = useToolDetailStore((s) => s.open)
  const close = useToolDetailStore((s) => s.close)

  return {
    isOpen,
    toolName,
    toolArgs,
    toolResult,
    contentType,
    open,
    close,
  }
}
