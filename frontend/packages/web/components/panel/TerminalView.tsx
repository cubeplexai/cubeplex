import { CommandRequestView } from './CommandRequestView'

interface TerminalViewProps {
  args: Record<string, unknown>
  result: string | null
}

export function TerminalView({ args, result }: TerminalViewProps) {
  // Parse exit code from result if present
  const exitMatch = result?.match(/\[exit:\s*(\d+)\]\s*$/)
  const exitCode = exitMatch ? exitMatch[1] : null
  const output = exitMatch ? result!.slice(0, exitMatch.index).trimEnd() : result

  return (
    <div className="p-4 space-y-3">
      <CommandRequestView args={args} />
      {output && (
        <div className="bg-muted rounded-lg p-3">
          <pre
            className="font-mono text-sm text-foreground
              whitespace-pre-wrap break-all"
          >
            {output}
          </pre>
        </div>
      )}
      {exitCode !== null && (
        <div
          className="text-xs text-muted-foreground
            text-right"
        >
          exit: {exitCode}
        </div>
      )}
    </div>
  )
}
