'use client'

import type { SpanNode } from './types'
import { JsonBlock } from './cards/JsonBlock'

interface Props {
  node: SpanNode
}

export function SpanDetail({ node }: Props) {
  return (
    <div className="space-y-4 p-4">
      <div>
        <h2 className="text-base font-semibold">{node.name}</h2>
        <p className="text-xs text-muted-foreground">
          {node.kind} · {node.duration_ms} ms · {new Date(node.start_time).toLocaleString()}
        </p>
      </div>
      {node.kind === 'other' && <JsonBlock value={JSON.stringify(node.raw_attributes, null, 2)} />}
      {/* LLM and tool cards land in Task 15. */}
    </div>
  )
}
