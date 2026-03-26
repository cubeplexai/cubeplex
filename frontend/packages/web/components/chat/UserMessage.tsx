interface UserMessageProps {
  content: string
}

export function UserMessage({ content }: UserMessageProps) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[72%] bg-primary text-white rounded-2xl rounded-br-sm px-3.5 py-2.5 text-sm leading-relaxed">
        {content}
      </div>
    </div>
  )
}
