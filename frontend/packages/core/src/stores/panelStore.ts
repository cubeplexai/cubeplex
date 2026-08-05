// frontend/packages/core/src/stores/panelStore.ts
import { create } from 'zustand'
import type { PanelContentType, ToolCallRef } from '../types'
import { bareToolName } from '../lib/toolName'

/** Map tool name + optional backend content_type to a PanelContentType. */
function mapContentType(toolName: string, backendContentType?: string): PanelContentType {
  const bare = bareToolName(toolName)
  if (bare === 'load_skill') return 'skill'
  if (bare === 'execute') return 'terminal'
  if (bare === 'write_file') return 'write_file'
  if (bare === 'edit_file') return 'edit_file'
  if (bare === 'code_execute' || bare === 'python') return 'code_execute'
  if (bare === 'file_read') return 'file_read'
  if (backendContentType === 'file_read') return 'file_read'

  if (backendContentType === 'json') {
    if (bare === 'web_search' || bare === 'search') return 'search'
    return 'generic'
  }
  if (backendContentType === 'text') {
    if (bare === 'web_fetch' || bare === 'fetch') return 'web_fetch'
    return 'generic'
  }

  if (bare === 'web_search' || bare === 'search') return 'search'
  if (bare === 'web_fetch' || bare === 'fetch') return 'web_fetch'
  return 'generic'
}

export interface AttachmentPanelInfo {
  attachmentId: string
  filename: string
  downloadUrl: string
  mimeType: string
  sizeBytes: number
  workspaceId?: string
  conversationId?: string
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
      highlightKey: number
    }
  | {
      type: 'artifact'
      conversationId: string
      artifactId: string
      /** Who opened the panel: user click/deep-link vs auto-open on save. */
      source: 'user' | 'auto'
    }
  | {
      type: 'attachment'
      info: AttachmentPanelInfo
    }
  | {
      type: 'sandbox'
      initialFilePath?: string
      /** Which sandbox tab to show when the panel opens / is re-targeted. */
      initialTab?: SandboxTab
      revision?: number
    }
  | { type: 'skill-candidate'; candidateId: string; repo: string | null; sourceName: string }

/** Tabs inside the sandbox side panel (Files / Browser / Terminal). */
export type SandboxTab = 'files' | 'browser' | 'terminal'

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

  openArtifact: (conversationId: string, artifactId: string, source?: 'user' | 'auto') => void

  openAttachment: (info: AttachmentPanelInfo) => void

  /**
   * Open the sandbox panel. Optional `tab` selects Files / Browser / Terminal
   * (defaults to files). Bumps revision so re-clicking while already open
   * still switches tabs.
   */
  openSandbox: (tab?: SandboxTab) => void

  openSandboxFile: (path: string) => void

  openSkillCandidate: (candidateId: string, repo: string | null, sourceName: string) => void

  close: () => void
}

let highlightCounter = 0
let sandboxRevisionCounter = 0

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
        highlightKey: ++highlightCounter,
      },
    }),

  openArtifact: (conversationId, artifactId, source = 'user') =>
    set({
      view: { type: 'artifact', conversationId, artifactId, source },
    }),

  openAttachment: (info) =>
    set({
      view: { type: 'attachment', info },
    }),

  openSandbox: (tab = 'files') =>
    set({
      view: {
        type: 'sandbox',
        initialTab: tab,
        revision: ++sandboxRevisionCounter,
      },
    }),

  openSandboxFile: (path) =>
    set({
      view: {
        type: 'sandbox',
        initialFilePath: path,
        initialTab: 'files',
        revision: ++sandboxRevisionCounter,
      },
    }),

  openSkillCandidate: (candidateId, repo, sourceName) =>
    set({ view: { type: 'skill-candidate', candidateId, repo, sourceName } }),

  close: () => set({ view: { type: 'closed' } }),
}))
