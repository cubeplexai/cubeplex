interface UserMessageProps {
  content: string
}

export function UserMessage({ content }: UserMessageProps) {
  return (
    <div className="flex justify-end">
      <div
        className={
          'max-w-[72%] bg-card border border-border rounded-md px-3.5 py-2.5 ' +
          'text-[13.5px] leading-relaxed text-foreground whitespace-pre-wrap'
        }
      >
        {content}
      </div>
    </div>
  )
}
