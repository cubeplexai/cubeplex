import { CopyButton, TimeChip } from './MessageMeta'

interface UserMessageProps {
  content: string
  timestamp?: number | null
}

export function UserMessage({ content, timestamp }: UserMessageProps) {
  return (
    <div data-role="user" className="group relative flex flex-col items-end gap-1">
      <div className="max-w-[88%] md:max-w-[78%] rounded-lg rounded-br-xs border border-border bg-raised px-3.5 py-2.5 text-md leading-relaxed">
        {content}
      </div>
      <div
        className="flex items-center gap-1 opacity-0 transition-opacity
          group-hover:opacity-100 focus-within:opacity-100"
      >
        <CopyButton content={content} />
        <TimeChip timestamp={timestamp ?? null} />
      </div>
    </div>
  )
}
