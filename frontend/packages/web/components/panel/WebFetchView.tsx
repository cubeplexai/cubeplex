import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface WebFetchViewProps {
  args: Record<string, unknown>
  result: string | null
}

const proseClasses = [
  'prose prose-sm dark:prose-invert max-w-none',
  'prose-p:leading-relaxed prose-p:my-1',
  'prose-headings:font-semibold prose-headings:mt-3',
  'prose-headings:mb-1 prose-headings:text-foreground',
  'prose-p:text-foreground prose-li:text-foreground',
  'prose-strong:text-foreground',
  'prose-code:text-foreground prose-code:text-[0.8em]',
  'prose-code:bg-muted prose-code:px-1',
  'prose-code:py-0.5 prose-code:rounded',
  'prose-code:before:content-none',
  'prose-code:after:content-none',
  'prose-pre:bg-muted prose-pre:border',
  'prose-pre:border-border prose-pre:rounded-lg',
  'prose-pre:text-[0.8em]',
  'prose-ul:my-1 prose-ol:my-1 prose-li:my-0',
  'prose-a:text-primary',
].join(' ')

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
