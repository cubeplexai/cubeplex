// frontend/packages/core/src/stores/panelStore.ts
import { create } from 'zustand'
import type { PanelContentType, ToolCallRef } from '../types'

/** Map tool name + optional backend content_type to a PanelContentType. */
function mapContentType(
  toolName: string,
  backendContentType?: string,
): PanelContentType {
  if (toolName === 'load_skill') return 'skill'
  if (toolName === 'execute') return 'terminal'
  if (toolName === 'write_file') return 'write_file'
  if (toolName === 'code_execute' || toolName === 'python') return 'code_execute'

  if (backendContentType === 'json') {
    if (toolName === 'web_search' || toolName === 'search') return 'search'
    return 'generic'
  }
  if (backendContentType === 'text') {
    if (toolName === 'web_fetch' || toolName === 'fetch') return 'web_fetch'
    return 'generic'
  }

  if (toolName === 'web_search' || toolName === 'search') return 'search'
  if (toolName === 'web_fetch' || toolName === 'fetch') return 'web_fetch'
  return 'generic'
}

export type PanelView =
  | { type: 'closed' }
  | {
      type: 'tool'
      toolName: string
      toolArgs: Record<string, unknown>
      toolResult: string | null
      contentType: PanelContentType
      toolRef: ToolCallRef | null
      highlightText: string | null
    }
  | {
      type: 'artifact'
      conversationId: string
      artifactId: string
    }

export interface PanelStore {
  view: PanelView

  openTool: (
    toolName: string,
    toolArgs: Record<string, unknown>,
    toolResult: string | null,
    contentType?: string,
    toolRef?: ToolCallRef,
    highlightText?: string,
  ) => void

  openArtifact: (conversationId: string, artifactId: string) => void

  close: () => void
}

export const usePanelStore = create<PanelStore>((set) => ({
  view: { type: 'closed' },

  openTool: (toolName, toolArgs, toolResult, contentType, toolRef, highlightText) =>
    set({
      view: {
        type: 'tool',
        toolName,
        toolArgs,
        toolResult,
        contentType: mapContentType(toolName, contentType),
        toolRef: toolRef ?? null,
        highlightText: highlightText ?? null,
      },
    }),

  openArtifact: (conversationId, artifactId) =>
    set({
      view: { type: 'artifact', conversationId, artifactId },
    }),

  close: () => set({ view: { type: 'closed' } }),
}))
