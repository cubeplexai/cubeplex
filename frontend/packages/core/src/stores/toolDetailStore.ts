// frontend/packages/core/src/stores/toolDetailStore.ts
// Thin compatibility layer — delegates to the unified panelStore.
import { usePanelStore } from './panelStore'
import type { PanelContentType, ToolCallRef } from '../types'

export interface ToolDetailStore {
  isOpen: boolean
  toolName: string
  toolArgs: Record<string, unknown>
  toolResult: string | null
  contentType: PanelContentType
  toolRef: ToolCallRef | null
  highlightText: string | null

  open: (
    toolName: string,
    toolArgs: Record<string, unknown>,
    toolResult: string | null,
    contentType?: string,
    toolRef?: ToolCallRef,
    highlightText?: string,
  ) => void
  close: () => void
}

export const useToolDetailStore = Object.assign(
  function useToolDetailStoreHook<T>(selector: (s: ToolDetailStore) => T): T {
    return usePanelStore((panel) => {
      const v = panel.view
      const facade: ToolDetailStore =
        v.type === 'tool'
          ? {
              isOpen: true,
              toolName: v.toolName,
              toolArgs: v.toolArgs,
              toolResult: v.toolResult,
              contentType: v.contentType,
              toolRef: v.toolRef,
              highlightText: v.highlightText,
              open: panel.openTool,
              close: panel.close,
            }
          : {
              isOpen: false,
              toolName: '',
              toolArgs: {},
              toolResult: null,
              contentType: 'generic',
              toolRef: null,
              highlightText: null,
              open: panel.openTool,
              close: panel.close,
            }
      return selector(facade)
    })
  },
  {
    getState(): ToolDetailStore {
      const panel = usePanelStore.getState()
      const v = panel.view
      if (v.type === 'tool') {
        return {
          isOpen: true,
          toolName: v.toolName,
          toolArgs: v.toolArgs,
          toolResult: v.toolResult,
          contentType: v.contentType,
          toolRef: v.toolRef,
          highlightText: v.highlightText,
          open: panel.openTool,
          close: panel.close,
        }
      }
      return {
        isOpen: false,
        toolName: '',
        toolArgs: {},
        toolResult: null,
        contentType: 'generic',
        toolRef: null,
        highlightText: null,
        open: panel.openTool,
        close: panel.close,
      }
    },
  },
)
