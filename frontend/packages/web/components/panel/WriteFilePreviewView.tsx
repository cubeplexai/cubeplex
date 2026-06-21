'use client'

import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useMessageStore } from '@cubebox/core'
import type { ToolCallRef } from '@cubebox/core'
import { proseClasses } from '@/lib/utils'
import { MarkdownWithCitations } from '@/components/shared/MarkdownWithCitations'
import { parseWriteFileArgs, resolveLiveWriteFile } from '@/lib/writeFilePreview'
import { useSandboxMarkdownContext } from '@/hooks/useSandboxMarkdownContext'

interface WriteFilePreviewViewProps {
  args: Record<string, unknown>
  result: string | null
  toolRef: ToolCallRef | null
}

type PreviewMode = 'markdown' | 'code' | 'text'

interface Token {
  type: 'plain' | 'comment' | 'string' | 'keyword' | 'number' | 'tag' | 'property'
  value: string
}

function detectLanguage(filePath: string): string | null {
  const ext = filePath.split('.').pop()?.toLowerCase() ?? ''
  const map: Record<string, string> = {
    js: 'javascript',
    jsx: 'javascript',
    ts: 'typescript',
    tsx: 'typescript',
    py: 'python',
    rb: 'ruby',
    go: 'go',
    rs: 'rust',
    java: 'java',
    kt: 'kotlin',
    swift: 'swift',
    php: 'php',
    c: 'c',
    h: 'c',
    cpp: 'cpp',
    cc: 'cpp',
    cxx: 'cpp',
    hpp: 'cpp',
    cs: 'csharp',
    sh: 'bash',
    bash: 'bash',
    zsh: 'bash',
    json: 'json',
    html: 'html',
    xml: 'html',
    css: 'css',
    scss: 'css',
    sql: 'sql',
    yaml: 'yaml',
    yml: 'yaml',
  }
  return map[ext] ?? null
}

function detectMode(filePath: string): PreviewMode {
  const normalized = filePath.toLowerCase()
  if (/\.(md|markdown|mdx)$/.test(normalized)) return 'markdown'
  if (detectLanguage(filePath)) return 'code'
  return 'text'
}

function getTokenPatterns(language: string | null): Array<{ type: Token['type']; regex: RegExp }> {
  if (language === 'python') {
    return [
      { type: 'comment', regex: /#[^\n]*/ },
      {
        type: 'string',
        regex: /"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/,
      },
      {
        type: 'keyword',
        regex:
          /\b(?:def|class|return|if|elif|else|for|while|import|from|as|try|except|finally|with|yield|lambda|pass|raise|True|False|None|async|await)\b/,
      },
      { type: 'number', regex: /\b\d+(?:\.\d+)?\b/ },
    ]
  }
  if (language === 'json' || language === 'yaml') {
    return [
      { type: 'string', regex: /"(?:\\.|[^"\\])*"/ },
      { type: 'keyword', regex: /\b(?:true|false|null)\b/ },
      { type: 'number', regex: /\b\d+(?:\.\d+)?\b/ },
      { type: 'property', regex: /\b[a-zA-Z_][\w-]*(?=\s*:)/ },
    ]
  }
  if (language === 'html') {
    return [
      { type: 'comment', regex: /<!--[\s\S]*?-->/ },
      { type: 'tag', regex: /<\/?[A-Za-z][^>]*?>/ },
      { type: 'string', regex: /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/ },
    ]
  }
  if (language === 'css') {
    return [
      { type: 'comment', regex: /\/\*[\s\S]*?\*\// },
      { type: 'string', regex: /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/ },
      { type: 'property', regex: /\b[a-z-]+(?=\s*:)/i },
      { type: 'number', regex: /#[0-9a-fA-F]{3,8}\b|\b\d+(?:\.\d+)?(?:px|rem|em|vh|vw|%)?\b/ },
    ]
  }

  return [
    { type: 'comment', regex: /\/\/[^\n]*|\/\*[\s\S]*?\*\// },
    { type: 'string', regex: /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`/ },
    {
      type: 'keyword',
      regex:
        /\b(?:const|let|var|function|return|if|else|for|while|import|from|export|default|class|extends|new|async|await|try|catch|throw|switch|case|break|continue|type|interface|implements|public|private|protected|readonly|true|false|null|undefined)\b/,
    },
    { type: 'number', regex: /\b\d+(?:\.\d+)?\b/ },
  ]
}

function tokenizeCode(code: string, language: string | null): Token[] {
  const patterns = getTokenPatterns(language)
  const tokens: Token[] = []
  let cursor = 0

  while (cursor < code.length) {
    let bestMatch: { start: number; end: number; type: Token['type']; value: string } | null = null

    for (const pattern of patterns) {
      const match = pattern.regex.exec(code.slice(cursor))
      if (!match || match.index == null) continue
      const start = cursor + match.index
      const end = start + match[0].length
      if (!bestMatch || start < bestMatch.start) {
        bestMatch = { start, end, type: pattern.type, value: match[0] }
      }
    }

    if (!bestMatch) {
      tokens.push({ type: 'plain', value: code.slice(cursor) })
      break
    }

    if (bestMatch.start > cursor) {
      tokens.push({ type: 'plain', value: code.slice(cursor, bestMatch.start) })
    }
    tokens.push({ type: bestMatch.type, value: bestMatch.value })
    cursor = bestMatch.end
  }

  return tokens
}

function tokenClassName(type: Token['type']): string {
  switch (type) {
    case 'comment':
      return 'text-success-fg'
    case 'string':
      return 'text-warning-fg'
    case 'keyword':
      return 'text-info-fg'
    case 'number':
      return 'text-info-fg'
    case 'tag':
      return 'text-danger-fg'
    case 'property':
      return 'text-info-fg'
    default:
      return 'text-foreground'
  }
}

function CodePreview({ code, language }: { code: string; language: string | null }) {
  const tokens = useMemo(() => tokenizeCode(code, language), [code, language])

  return (
    <pre
      className="p-4 text-xs leading-relaxed font-mono whitespace-pre-wrap break-words
        bg-muted/40"
    >
      {tokens.map((token, index) => (
        <span key={index} className={tokenClassName(token.type)}>
          {token.value}
        </span>
      ))}
    </pre>
  )
}

export function WriteFilePreviewView({ args, result, toolRef }: WriteFilePreviewViewProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)
  const prevStreamingRef = useRef(false)
  const liveBlocks = useMessageStore((s) => {
    const agentId = toolRef?.agent_id
    return agentId ? (s.streamAgents[agentId]?.blocks ?? []) : []
  })

  const parsed = useMemo(() => {
    const live = resolveLiveWriteFile(liveBlocks, toolRef)
    if (live) {
      return result ? { ...live, status: 'completed' as const } : live
    }
    const fallback = parseWriteFileArgs(args)
    return result ? { ...fallback, status: 'completed' as const } : fallback
  }, [args, liveBlocks, result, toolRef])

  const filePath = parsed.filePath || 'Untitled file'
  const content = parsed.content
  const mode = detectMode(filePath)
  const language = detectLanguage(filePath)
  const isStreaming = parsed.status === 'streaming_args'
  const sandboxCtx = useSandboxMarkdownContext(parsed.filePath || null)

  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const threshold = 80
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < threshold
  }, [])

  useEffect(() => {
    const contentEl = contentRef.current
    const scrollEl = scrollRef.current
    if (!contentEl || !scrollEl) return

    const ro = new ResizeObserver(() => {
      if (stickToBottom.current) {
        scrollEl.scrollTop = scrollEl.scrollHeight
      }
    })
    ro.observe(contentEl)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    if (isStreaming && !prevStreamingRef.current) {
      stickToBottom.current = true
      if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight
      }
    }
    prevStreamingRef.current = isStreaming
  }, [isStreaming])

  return (
    <div ref={scrollRef} className="h-full overflow-auto" onScroll={handleScroll}>
      <div ref={contentRef}>
        <div className="px-4 py-3 border-b border-border bg-card">
          <div className="text-sm font-medium text-foreground truncate">{filePath}</div>
        </div>

        {mode === 'markdown' ? (
          <MarkdownWithCitations
            className={`p-4 ${proseClasses}`}
            sandbox={sandboxCtx ?? undefined}
          >
            {content}
          </MarkdownWithCitations>
        ) : mode === 'code' ? (
          <CodePreview code={content} language={language} />
        ) : (
          <pre className="p-4 text-sm leading-relaxed whitespace-pre-wrap break-words text-foreground">
            {content}
          </pre>
        )}
      </div>
    </div>
  )
}
