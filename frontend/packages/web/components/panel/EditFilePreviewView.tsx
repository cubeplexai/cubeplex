'use client'

import type { ToolCallRef } from '@cubeplex/core'
import { useMessageStore } from '@cubeplex/core'
import { DiffViewer } from './DiffViewer'

interface EditFilePreviewViewProps {
  args: Record<string, unknown>
  result: string | null
  toolRef: ToolCallRef | null
}

interface EditFileDetails {
  file_path?: string
  unified_diff?: string
  fuzzy_matched?: boolean
}

function isEditFileDetails(v: unknown): v is EditFileDetails {
  return typeof v === 'object' && v !== null
}

export function EditFilePreviewView({ args, result, toolRef }: EditFilePreviewViewProps) {
  const toolCallId = toolRef?.tool_call_id ?? null
  const details = useMessageStore((s) =>
    toolCallId ? s.toolResultMap[toolCallId]?.details : undefined,
  )

  const filePath = typeof args.file_path === 'string' ? args.file_path : (result ?? 'Unknown file')

  const editDetails = isEditFileDetails(details) ? details : null
  const unifiedDiff = editDetails?.unified_diff
  const fuzzyMatched = editDetails?.fuzzy_matched === true

  // Fallback: show old_string → new_string plaintext if diff isn't available yet
  const oldString = typeof args.old_string === 'string' ? args.old_string : null
  const newString = typeof args.new_string === 'string' ? args.new_string : null

  return (
    <div className="h-full overflow-auto">
      <div className="px-4 py-3 border-b border-border bg-card flex items-center gap-2">
        <div className="text-sm font-medium text-foreground truncate flex-1">{filePath}</div>
        {fuzzyMatched && (
          <span className="shrink-0 text-xs px-1.5 py-0.5 rounded bg-warning-surface text-warning-fg font-medium">
            fuzzy match
          </span>
        )}
      </div>

      {unifiedDiff ? (
        <DiffViewer diff={unifiedDiff} />
      ) : result ? (
        // Tool completed but no diff — shouldn't happen, show fallback
        <div className="p-4 text-sm text-muted-foreground">{result}</div>
      ) : (
        // Tool still pending: show old_string → new_string plaintext
        <div className="p-4 space-y-3">
          {oldString !== null && (
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">Old</div>
              <pre className="text-xs bg-destructive/8 text-destructive p-3 rounded overflow-x-auto whitespace-pre">
                {oldString}
              </pre>
            </div>
          )}
          {newString !== null && (
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">New</div>
              <pre className="text-xs bg-success-surface/60 text-success-fg p-3 rounded overflow-x-auto whitespace-pre">
                {newString}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
