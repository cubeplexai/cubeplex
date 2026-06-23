'use client'

import { useState, useEffect } from 'react'

const EXT_TO_LANG: Record<string, string> = {
  py: 'python',
  js: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  jsx: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  rb: 'ruby',
  go: 'go',
  rs: 'rust',
  java: 'java',
  kt: 'kotlin',
  c: 'c',
  cpp: 'cpp',
  h: 'c',
  hpp: 'cpp',
  cs: 'csharp',
  swift: 'swift',
  sh: 'bash',
  bash: 'bash',
  zsh: 'bash',
  html: 'xml',
  css: 'css',
  scss: 'scss',
  less: 'less',
  sql: 'sql',
  json: 'json',
  yaml: 'yaml',
  yml: 'yaml',
  toml: 'ini',
  xml: 'xml',
  vue: 'xml',
  svelte: 'xml',
  php: 'php',
  r: 'r',
  lua: 'lua',
  pl: 'perl',
  ex: 'elixir',
  exs: 'elixir',
  dockerfile: 'dockerfile',
  makefile: 'makefile',
}

function getLang(filename: string): string | undefined {
  const dot = filename.lastIndexOf('.')
  if (dot < 0) return undefined
  return EXT_TO_LANG[filename.slice(dot + 1).toLowerCase()]
}

interface CodeHighlightProps {
  code: string
  filename: string
}

export function CodeHighlight({ code, filename }: CodeHighlightProps) {
  const [html, setHtml] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const lang = getLang(filename)
    import('highlight.js/lib/common')
      .then((mod) => {
        if (cancelled) return
        const hljs = mod.default
        let result: { value: string }
        if (lang) {
          try {
            result = hljs.highlight(code, { language: lang })
          } catch {
            result = hljs.highlightAuto(code)
          }
        } else {
          result = hljs.highlightAuto(code)
        }
        setHtml(result.value)
      })
      .catch(() => {
        // highlight.js failed to load — plain text fallback stays visible
      })
    return () => {
      cancelled = true
    }
  }, [code, filename])

  const sourceLines = code.split('\n')
  const displayLines = html !== null ? html.split('\n') : sourceLines
  const gutterWidth = String(sourceLines.length).length

  return (
    <div className="h-full overflow-auto py-2">
      <table className="w-full border-collapse text-xs font-mono leading-relaxed">
        <tbody>
          {displayLines.map((line, i) => (
            <tr key={i} className="hover:bg-muted/30">
              <td
                className="select-none pr-4 text-right text-muted-foreground/50 align-top
                  sticky left-0 bg-background"
                style={{
                  width: `${gutterWidth + 2}ch`,
                  minWidth: `${gutterWidth + 2}ch`,
                  paddingLeft: '0.75rem',
                }}
              >
                {i + 1}
              </td>
              {html !== null ? (
                <td
                  className="whitespace-pre-wrap break-words pl-2 pr-4"
                  dangerouslySetInnerHTML={{ __html: line || '&nbsp;' }}
                />
              ) : (
                <td className="whitespace-pre-wrap break-words text-foreground pl-2 pr-4">
                  {line || ' '}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
