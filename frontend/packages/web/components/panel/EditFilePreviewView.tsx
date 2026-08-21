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
  edit_count?: number
  match_mode?: 'exact' | 'fuzzy'
  first_changed_line?: number | null
}

interface EditSpec {
  old_string: string
  new_string: string
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

  const edits = getEditSpecs(args)

  return (
    <div className="h-full overflow-auto">
      <div className="px-4 py-3 border-b border-border bg-card flex items-center gap-2">
        <div className="text-sm font-medium text-foreground truncate flex-1">{filePath}</div>
        {fuzzyMatched && (
          <span className="shrink-0 text-xs px-1.5 py-0.5 rounded bg-warning-surface text-warning-fg font-medium">
            fuzzy match
          </span>
        )}
        {editDetails?.edit_count && editDetails.edit_count > 1 && (
          <span className="shrink-0 text-xs text-muted-foreground">
            {editDetails.edit_count} edits
          </span>
        )}
        {editDetails?.first_changed_line != null && (
          <span className="shrink-0 text-xs text-muted-foreground">
            line {editDetails.first_changed_line}
          </span>
        )}
      </div>

      {unifiedDiff ? (
        <DiffViewer diff={unifiedDiff} />
      ) : result ? (
        // Tool completed but no diff — shouldn't happen, show fallback
        <div className="p-4 text-sm text-muted-foreground">{result}</div>
      ) : (
        // Tool still pending: show every old_string → new_string pair.
        <div className="p-4 space-y-4">
          {edits.map((edit, index) => (
            <div key={`${index}-${edit.old_string}`} className="space-y-2">
              {edits.length > 1 && (
                <div className="text-xs font-medium text-muted-foreground">Edit {index + 1}</div>
              )}
              <div>
                <div className="text-xs font-medium text-muted-foreground mb-1">Old</div>
                <pre className="text-xs bg-destructive/8 text-destructive p-3 rounded overflow-x-auto whitespace-pre">
                  {edit.old_string}
                </pre>
              </div>
              <div>
                <div className="text-xs font-medium text-muted-foreground mb-1">New</div>
                <pre className="text-xs bg-success-surface/60 text-success-fg p-3 rounded overflow-x-auto whitespace-pre">
                  {edit.new_string}
                </pre>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function isEditSpec(value: unknown): value is EditSpec {
  return (
    typeof value === 'object' &&
    value !== null &&
    'old_string' in value &&
    'new_string' in value &&
    typeof value.old_string === 'string' &&
    typeof value.new_string === 'string'
  )
}

export function getEditSpecs(args: Record<string, unknown>): EditSpec[] {
  if (Array.isArray(args.edits)) return args.edits.filter(isEditSpec)
  if (typeof args.old_string === 'string' && typeof args.new_string === 'string') {
    return [{ old_string: args.old_string, new_string: args.new_string }]
  }
  return []
}
