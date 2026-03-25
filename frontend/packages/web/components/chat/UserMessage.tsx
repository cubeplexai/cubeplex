interface UserMessageProps {
  content: string
}

export function UserMessage({ content }: UserMessageProps) {
  return (
    <div className="flex justify-end">
      <div className="bg-primary/10 border border-primary/30 text-foreground rounded-lg px-4 py-2 max-w-xs">
        {content}
      </div>
    </div>
  )
}
