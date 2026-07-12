'use client'

import { wsInvokeTool, type ApiClient, type ToolInvokeResult } from '@cubeplex/core'

import { TryItForm } from './TryItForm'

export interface WsTryItViewProps {
  connectorId: string
  toolName: string
  inputSchema: Record<string, unknown> | null
  client: ApiClient
  wsId: string
}

export function WsTryItView({
  connectorId,
  toolName,
  inputSchema,
  client,
  wsId,
}: WsTryItViewProps) {
  const onRun = async (args: Record<string, unknown>): Promise<ToolInvokeResult> =>
    wsInvokeTool(client, wsId, connectorId, toolName, args)

  return <TryItForm toolName={toolName} inputSchema={inputSchema} onRun={onRun} />
}
