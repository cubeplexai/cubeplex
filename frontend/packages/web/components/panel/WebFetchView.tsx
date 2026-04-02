import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface WebFetchViewProps {
  args: Record<string, unknown>
  result: string | null
}

import { proseClasses } from '@/lib/utils'

export function WebFetchView({
  args,
  result,
}: WebFetchViewProps) {
  const url = String(args.url ?? '')

  return (
    <div className="p-4 space-y-3">
      {url && (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-primary
            hover:underline break-all"
        >
          {url}
        </a>
      )}
      {result && (
        <div className={proseClasses}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {result}
          </ReactMarkdown>
        </div>
      )}
    </div>
  )
}
