'use client'

import { Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

interface Props {
  metaField: string
  outputField: string
  outputFieldCandidates: string[] | null
  onMetaFieldChange: (v: string) => void
  onOutputFieldChange: (v: string) => void
  onRemove: () => void
  readOnly?: boolean
}

export function MCPCitationFieldRow({
  metaField,
  outputField,
  outputFieldCandidates,
  onMetaFieldChange,
  onOutputFieldChange,
  onRemove,
  readOnly,
}: Props) {
  return (
    <div className="flex items-center gap-2">
      <Input
        className="w-40"
        value={metaField}
        onChange={(e) => onMetaFieldChange(e.target.value)}
        readOnly={readOnly}
        aria-label="metadata field name"
      />
      <span className="text-muted-foreground">=</span>
      {outputFieldCandidates ? (
        <select
          className="flex-1 rounded-md border px-2 py-1"
          value={outputField}
          onChange={(e) => onOutputFieldChange(e.target.value)}
          disabled={readOnly}
          aria-label="output field"
        >
          <option value="">—</option>
          {outputFieldCandidates.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
      ) : (
        <Input
          className="flex-1"
          value={outputField}
          onChange={(e) => onOutputFieldChange(e.target.value)}
          readOnly={readOnly}
          aria-label="output field"
        />
      )}
      {!readOnly && (
        <Button variant="ghost" size="icon" onClick={onRemove} aria-label="remove field">
          <Trash2 className="h-4 w-4" />
        </Button>
      )}
    </div>
  )
}
