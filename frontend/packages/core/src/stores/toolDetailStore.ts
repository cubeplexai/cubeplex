// frontend/packages/core/src/stores/toolDetailStore.ts
import { create } from 'zustand'
import type { PanelContentType } from '../types'

export interface ToolDetailStore {
  isOpen: boolean
  toolName: string
  toolArgs: Record<string, unknown>
  toolResult: string | null
  contentType: PanelContentType

  open: (
    toolName: string,
    toolArgs: Record<string, unknown>,
    toolResult: string | null,
  ) => void
  close: () => void
}

function detectContentType(
  toolName: string,
): PanelContentType {
  if (toolName === 'execute') return 'terminal'
  if (toolName === 'web_search' || toolName === 'search') {
    return 'search'
  }
  if (toolName === 'web_fetch' || toolName === 'fetch') {
    return 'web_fetch'
  }
  if (
    toolName === 'code_execute' || toolName === 'python'
  ) {
    return 'code_execute'
  }
  return 'generic'
}

export const useToolDetailStore =
  create<ToolDetailStore>((set) => ({
    isOpen: false,
    toolName: '',
    toolArgs: {},
    toolResult: null,
    contentType: 'generic',

    open: (toolName, toolArgs, toolResult) =>
      set({
        isOpen: true,
        toolName,
        toolArgs,
        toolResult,
        contentType: detectContentType(toolName),
      }),

    close: () => set({ isOpen: false }),
  }))
