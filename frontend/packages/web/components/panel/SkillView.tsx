import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { proseClasses } from '@/lib/utils'

interface SkillViewProps {
  args: Record<string, unknown>
  result: string | null
}

interface SkillResult {
  skill_name: string
  content: string
  loaded: boolean
  error: string | null
}

function parseResult(
  result: string | null,
): SkillResult | null {
  if (!result) return null
  try {
    return JSON.parse(result) as SkillResult
  } catch {
    return null
  }
}

export function SkillView({
  args,
  result,
}: SkillViewProps) {
  const skillName = String(args.skill_name ?? '')
  const parsed = parseResult(result)

  return (
    <div className="p-4 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-muted-foreground">
          Skill:
        </span>
        <span className="text-sm font-mono font-semibold">
          {parsed?.skill_name ?? skillName}
        </span>
        {parsed && (
          <span
            className={`text-xs px-1.5 py-0.5 rounded-full ${
              parsed.loaded
                ? 'bg-green-500/10 text-green-600 dark:text-green-400'
                : 'bg-red-500/10 text-red-600 dark:text-red-400'
            }`}
          >
            {parsed.loaded ? 'loaded' : 'failed'}
          </span>
        )}
      </div>

      {parsed?.error && (
        <div className="text-sm text-red-600 dark:text-red-400 bg-red-500/10 rounded-md p-3">
          {parsed.error}
        </div>
      )}

      {parsed?.content && (
        <div className={proseClasses}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {parsed.content}
          </ReactMarkdown>
        </div>
      )}

      {!parsed && result && (
        <pre className="text-xs whitespace-pre-wrap text-muted-foreground">
          {result}
        </pre>
      )}
    </div>
  )
}
