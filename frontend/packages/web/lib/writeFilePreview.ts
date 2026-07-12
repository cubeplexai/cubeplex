import type { ContentBlock, ToolCallRef } from '@cubeplex/core'
import { extractJsonStringPrefix } from '@/lib/partialJson'

export type WriteFileStatus = 'streaming_args' | 'pending_execution' | 'completed'

export interface ParsedWriteFile {
  filePath: string
  content: string
  status: WriteFileStatus
}

function readStringArg(args: Record<string, unknown>, key: string): string {
  return typeof args[key] === 'string' ? args[key] : ''
}

function readFilePath(args: Record<string, unknown>): string {
  return readStringArg(args, 'file_path') || readStringArg(args, 'file_name')
}

export function parseWriteFileArgs(
  args: Record<string, unknown>,
  rawArgsText?: string | null,
): ParsedWriteFile {
  const filePathFromArgs = readFilePath(args)
  const contentFromArgs = readStringArg(args, 'content')

  if (filePathFromArgs || contentFromArgs) {
    return {
      filePath: filePathFromArgs,
      content: contentFromArgs,
      status: 'pending_execution',
    }
  }

  const raw = rawArgsText ?? ''
  return {
    filePath:
      extractJsonStringPrefix(raw, 'file_path') || extractJsonStringPrefix(raw, 'file_name'),
    content: extractJsonStringPrefix(raw, 'content'),
    status: 'streaming_args',
  }
}

export function resolveLiveWriteFile(
  blocks: ContentBlock[],
  toolRef: ToolCallRef | null,
): ParsedWriteFile | null {
  if (!toolRef) return null

  for (let i = blocks.length - 1; i >= 0; i--) {
    const block = blocks[i]
    if (block.type === 'tool_call' && block.name === 'write_file') {
      if (toolRef.tool_call_id && block.id === toolRef.tool_call_id) {
        return {
          filePath: readFilePath(block.arguments),
          content: readStringArg(block.arguments, 'content'),
          status: 'pending_execution',
        }
      }
    }
    if (block.type === 'tool_call_streaming' && block.name === 'write_file') {
      const sameId = toolRef.tool_call_id && block.tool_call_id === toolRef.tool_call_id
      const sameIndex = toolRef.index != null && block.index === toolRef.index
      if (sameId || sameIndex) {
        return parseWriteFileArgs({}, block.args_text)
      }
    }
  }

  return null
}

export function getWriteFileSummary(
  args: Record<string, unknown>,
  rawArgsText?: string | null,
): string {
  const parsed = parseWriteFileArgs(args, rawArgsText)
  if (parsed.filePath) return parsed.filePath

  const firstLine = parsed.content.split('\n')[0]?.trim() ?? ''
  if (firstLine) {
    return firstLine.length > 60 ? `${firstLine.slice(0, 60)}...` : firstLine
  }

  return 'Preparing file...'
}
