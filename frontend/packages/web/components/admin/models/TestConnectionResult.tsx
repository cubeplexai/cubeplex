import { CheckCircle2, XCircle } from 'lucide-react'
import type { TestResult } from '@cubebox/core'

interface TestConnectionResultProps {
  result: TestResult | null
  busy: boolean
}

export function TestConnectionResult({ result, busy }: TestConnectionResultProps) {
  if (busy) {
    return (
      <div
        data-testid="test-result"
        className="flex items-center gap-2 rounded-md border border-border/60 bg-muted/30 px-3 py-2 text-xs text-muted-foreground"
      >
        <span className="size-3 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-muted-foreground" />
        测试中...
      </div>
    )
  }

  if (!result) return null

  if (result.ok) {
    return (
      <div
        data-testid="test-result"
        className="flex items-center gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 text-xs text-emerald-600 dark:text-emerald-400"
      >
        <CheckCircle2 className="size-4 shrink-0" />
        <span>连接成功 &middot; {result.latency_ms}ms</span>
      </div>
    )
  }

  return (
    <div
      data-testid="test-result"
      className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive"
    >
      <XCircle className="mt-0.5 size-4 shrink-0" />
      <span>{result.error}</span>
    </div>
  )
}
