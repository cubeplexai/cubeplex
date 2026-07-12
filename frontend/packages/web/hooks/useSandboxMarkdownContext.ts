'use client'

import { useCallback, useMemo } from 'react'
import { usePanelStore, useConversationStore } from '@cubeplex/core'
import type { SandboxMarkdownContext } from '@/components/shared/MarkdownWithCitations'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'

/**
 * Build a `sandbox` context for `<MarkdownWithCitations>` so relative links
 * inside a sandbox-resident markdown file (e.g. agent-written `index.md`)
 * navigate to the target file's sandbox preview instead of 404-ing through
 * the Next router.
 *
 * Returns null when we can't anchor the file in a sandbox (missing workspace
 * or unknown file path), so the markdown renders with default link behaviour.
 */
export function useSandboxMarkdownContext(
  filePath: string | null | undefined,
): SandboxMarkdownContext | null {
  const { workspaceId } = useWorkspaceContext()
  const conversationId = useConversationStore((s) => s.activeId)
  const openSandboxFile = usePanelStore((s) => s.openSandboxFile)

  const resolveAssetUrl = useCallback(
    (path: string) => {
      if (!workspaceId) return path
      const convQs = conversationId ? `&conversation_id=${encodeURIComponent(conversationId)}` : ''
      return (
        `/api/v1/ws/${workspaceId}` +
        `/sandbox/files/download` +
        `?path=${encodeURIComponent(path)}${convQs}`
      )
    },
    [workspaceId, conversationId],
  )

  return useMemo(() => {
    if (!workspaceId || !filePath) return null
    return {
      filePath,
      onNavigate: openSandboxFile,
      resolveAssetUrl,
    }
  }, [workspaceId, filePath, openSandboxFile, resolveAssetUrl])
}
