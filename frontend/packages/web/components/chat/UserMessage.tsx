interface UserMessageProps {
  content: string
}

export function UserMessage({ content }: UserMessageProps) {
  return (
    <div data-role="user" className="flex justify-end">
      <div className="max-w-[88%] md:max-w-[78%] rounded-lg rounded-br-xs border border-border bg-raised px-3.5 py-2.5 text-md leading-relaxed">
        {content}
      </div>
    </div>
  )
}
