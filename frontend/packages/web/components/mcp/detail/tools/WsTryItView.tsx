'use client'

import { wsInvokeTool, type ApiClient, type ToolInvokeResult } from '@cubebox/core'

import { TryItForm } from './TryItForm'

export interface WsTryItViewProps {
  installId: string
  toolName: string
  inputSchema: Record<string, unknown> | null
  client: ApiClient
  wsId: string
}

export function WsTryItView({ installId, toolName, inputSchema, client, wsId }: WsTryItViewProps) {
  const onRun = async (args: Record<string, unknown>): Promise<ToolInvokeResult> =>
    wsInvokeTool(client, wsId, installId, toolName, args)

  return <TryItForm toolName={toolName} inputSchema={inputSchema} onRun={onRun} />
}
