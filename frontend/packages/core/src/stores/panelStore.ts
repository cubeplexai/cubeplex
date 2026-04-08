// frontend/packages/core/src/stores/panelStore.ts
import { create } from 'zustand'
import type { PanelContentType } from '../types'

/** Map tool name + optional backend content_type to a PanelContentType. */
function mapContentType(
  toolName: string,
  backendContentType?: string,
): PanelContentType {
  if (toolName === 'load_skill') return 'skill'
  if (toolName === 'execute') return 'terminal'
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
  ) => void

  openArtifact: (conversationId: string, artifactId: string) => void

  close: () => void
}

export const usePanelStore = create<PanelStore>((set) => ({
  view: { type: 'closed' },

  openTool: (toolName, toolArgs, toolResult, contentType) =>
    set({
      view: {
        type: 'tool',
        toolName,
        toolArgs,
        toolResult,
        contentType: mapContentType(toolName, contentType),
      },
    }),

  openArtifact: (conversationId, artifactId) =>
    set({
      view: { type: 'artifact', conversationId, artifactId },
    }),

  close: () => set({ view: { type: 'closed' } }),
}))
