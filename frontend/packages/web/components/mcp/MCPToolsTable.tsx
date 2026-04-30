'use client'

import type { MCPToolEntry } from '@cubebox/core'

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion'

export function MCPToolsTable({ tools }: { tools: MCPToolEntry[] }) {
  if (tools.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No tools discovered yet. Refresh tools after the MCP server is reachable.
      </p>
    )
  }

  return (
    <Accordion className="w-full">
      {tools.map((tool) => (
        <AccordionItem key={tool.name} value={tool.name}>
          <AccordionTrigger>
            <span className="flex min-w-0 items-center gap-3">
              <span className="font-mono">{tool.name}</span>
              <span className="truncate text-sm text-muted-foreground">{tool.description}</span>
            </span>
          </AccordionTrigger>
          <AccordionContent>
            <pre className="overflow-x-auto rounded-md bg-muted/50 p-3 font-mono text-xs">
              {JSON.stringify(tool.input_schema, null, 2)}
            </pre>
          </AccordionContent>
        </AccordionItem>
      ))}
    </Accordion>
  )
}
