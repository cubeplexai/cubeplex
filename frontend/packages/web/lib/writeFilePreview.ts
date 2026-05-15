import type { ContentBlock, ToolCallRef } from '@cubebox/core'

export type WriteFileStatus = 'streaming_args' | 'pending_execution' | 'completed'

export interface ParsedWriteFile {
  filePath: string
  content: string
  status: WriteFileStatus
}

function readStringArg(args: Record<string, unknown>, key: string): string {
  return typeof args[key] === 'string' ? args[key] : ''
}

function decodeEscapeSequence(char: string): string {
  switch (char) {
    case 'n':
      return '\n'
    case 'r':
      return '\r'
    case 't':
      return '\t'
    case 'b':
      return '\b'
    case 'f':
      return '\f'
    case '"':
      return '"'
    case '\\':
      return '\\'
    case '/':
      return '/'
    default:
      return char
  }
}

function extractJsonStringPrefix(raw: string, key: string): string {
  const keyMatch = new RegExp(`"${key}"\\s*:\\s*"`, 'm').exec(raw)
  if (!keyMatch) return ''

  let i = keyMatch.index + keyMatch[0].length
  let value = ''

  while (i < raw.length) {
    const char = raw[i]
    if (char === '"') break
    if (char === '\\') {
      const next = raw[i + 1]
      if (!next) break
      if (next === 'u') {
        const hex = raw.slice(i + 2, i + 6)
        if (hex.length < 4 || /[^0-9a-fA-F]/.test(hex)) break
        value += String.fromCharCode(parseInt(hex, 16))
        i += 6
        continue
      }
      value += decodeEscapeSequence(next)
      i += 2
      continue
    }
    value += char
    i++
  }

  return value
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
