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
    contentType?: string,
  ) => void
  close: () => void
}

/** Map tool name + optional backend content_type to a PanelContentType. */
function mapContentType(
  toolName: string,
  backendContentType?: string,
): PanelContentType {
  // Built-in tools: detect from tool name
  if (toolName === 'load_skill') return 'skill'
  if (toolName === 'execute') return 'terminal'
  if (
    toolName === 'code_execute' || toolName === 'python'
  ) {
    return 'code_execute'
  }

  // MCP tools: use backend-declared content_type to pick panel
  if (backendContentType === 'json') {
    // JSON content: use tool-name-specific panels
    if (toolName === 'web_search' || toolName === 'search') {
      return 'search'
    }
    return 'generic'
  }
  if (backendContentType === 'text') {
    if (toolName === 'web_fetch' || toolName === 'fetch') {
      return 'web_fetch'
    }
    return 'generic'
  }

  // Fallback: guess from tool name
  if (toolName === 'web_search' || toolName === 'search') {
    return 'search'
  }
  if (toolName === 'web_fetch' || toolName === 'fetch') {
    return 'web_fetch'
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

    open: (toolName, toolArgs, toolResult, contentType) =>
      set({
        isOpen: true,
        toolName,
        toolArgs,
        toolResult,
        contentType: mapContentType(toolName, contentType),
      }),

    close: () => set({ isOpen: false }),
  }))
