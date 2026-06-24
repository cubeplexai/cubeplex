import { MessageActions } from './MessageActions'

interface UserMessageProps {
  content: string
  conversationId: string
  workspaceId: string | null
  runId: string | null | undefined
  isGroupChat: boolean
  activeRunId: string | null
  isStreaming: boolean
}

export function UserMessage({
  content,
  conversationId,
  workspaceId,
  runId,
  isGroupChat,
  activeRunId,
  isStreaming,
}: UserMessageProps) {
  return (
    <div data-role="user" className="group relative flex justify-end">
      <div className="max-w-[88%] md:max-w-[78%] rounded-lg rounded-br-xs border border-border bg-raised px-3.5 py-2.5 text-md leading-relaxed">
        {content}
      </div>
      <div
        className="absolute -bottom-1 right-0 translate-y-full opacity-0
          transition-opacity group-hover:opacity-100 focus-within:opacity-100"
      >
        <MessageActions
          conversationId={conversationId}
          workspaceId={workspaceId}
          runId={runId}
          isGroupChat={isGroupChat}
          activeRunId={activeRunId}
          isStreaming={isStreaming}
        />
      </div>
    </div>
  )
}
