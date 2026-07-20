'use client'

interface DiffViewerProps {
  diff: string
}

interface HunkLine {
  type: 'header' | 'removed' | 'added' | 'context' | 'file-header'
  content: string
  oldLine: number | null
  newLine: number | null
}

function parseUnifiedDiff(diff: string): HunkLine[] {
  const lines = diff.split('\n')
  const result: HunkLine[] = []
  let oldLine = 0
  let newLine = 0

  for (const raw of lines) {
    if (raw === '') continue

    if (raw.startsWith('--- ') || raw.startsWith('+++ ')) {
      result.push({ type: 'file-header', content: raw, oldLine: null, newLine: null })
      continue
    }

    if (raw.startsWith('@@ ')) {
      // Parse @@ -a,b +c,d @@
      const m = raw.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/)
      if (m) {
        oldLine = parseInt(m[1], 10)
        newLine = parseInt(m[2], 10)
      }
      result.push({ type: 'header', content: raw, oldLine: null, newLine: null })
      continue
    }

    if (raw.startsWith('-')) {
      result.push({ type: 'removed', content: raw.slice(1), oldLine: oldLine++, newLine: null })
    } else if (raw.startsWith('+')) {
      result.push({ type: 'added', content: raw.slice(1), oldLine: null, newLine: newLine++ })
    } else {
      const ctx = raw.startsWith(' ') ? raw.slice(1) : raw
      result.push({ type: 'context', content: ctx, oldLine: oldLine++, newLine: newLine++ })
    }
  }

  return result
}

export function DiffViewer({ diff }: DiffViewerProps) {
  const lines = parseUnifiedDiff(diff)

  return (
    <div className="font-mono text-xs overflow-x-auto">
      <table className="w-full border-collapse">
        <tbody>
          {lines.map((line, i) => {
            if (line.type === 'file-header') return null

            if (line.type === 'header') {
              return (
                <tr key={i} className="bg-muted">
                  <td className="w-10 text-right pr-2 text-muted-foreground select-none py-0.5 pl-2" />
                  <td className="w-10 text-right pr-2 text-muted-foreground select-none py-0.5" />
                  <td className="px-3 py-0.5 text-muted-foreground whitespace-pre">
                    {line.content}
                  </td>
                </tr>
              )
            }

            const rowClass =
              line.type === 'removed'
                ? 'bg-destructive/8'
                : line.type === 'added'
                  ? 'bg-success-surface/60'
                  : ''

            const textClass =
              line.type === 'removed'
                ? 'text-destructive'
                : line.type === 'added'
                  ? 'text-success-fg'
                  : ''

            const marker = line.type === 'removed' ? '-' : line.type === 'added' ? '+' : ' '

            return (
              <tr key={i} className={rowClass}>
                <td className="w-10 text-right pr-2 text-muted-foreground select-none py-0.5 pl-2 min-w-[2.5rem]">
                  {line.oldLine ?? ''}
                </td>
                <td className="w-10 text-right pr-2 text-muted-foreground select-none py-0.5 min-w-[2.5rem]">
                  {line.newLine ?? ''}
                </td>
                <td className={`px-3 py-0.5 whitespace-pre ${textClass}`}>
                  <span className="select-none mr-1">{marker}</span>
                  {line.content}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
