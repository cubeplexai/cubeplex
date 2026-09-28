'use client'

import { useEffect } from 'react'
import useSWR from 'swr'
import { useTranslations } from 'next-intl'
import {
  createApiClient,
  getBackgroundTask,
  getToolResultPreviewContent,
  useMessageStore,
} from '@cubeplex/core'
import type { BackgroundTask, Message } from '@cubeplex/core'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { useSandboxFileContent } from '@/hooks/useSandboxFileContent'
import { GenericToolView } from '@/components/panel/GenericToolView'
import { Button } from '@/components/ui/button'

const EMPTY_MESSAGES: Message[] = []

export function BackgroundTaskDetails({
  conversationId,
  taskId,
  task: providedTask,
}: {
  conversationId: string
  taskId: string
  task?: BackgroundTask
}) {
  const t = useTranslations('backgroundTasks')
  const { workspaceId } = useWorkspaceContext()
  const storedTask = useMessageStore((s) =>
    s.backgroundTasks?.[conversationId]?.find((task) => task.id === taskId),
  )
  const task = providedTask ?? storedTask
  const { data, error, isLoading, mutate } = useSWR(
    !task && workspaceId ? ['background-task-detail', workspaceId, conversationId, taskId] : null,
    async () => {
      const client = createApiClient('')
      client.setWorkspaceId(workspaceId!)
      return getBackgroundTask(client, conversationId, taskId)
    },
    { shouldRetryOnError: false },
  )
  const resolved = task ?? data
  if (!resolved) {
    return (
      <div className="space-y-2 text-xs text-muted-foreground">
        <p>{error ? t('detailsFailed') : isLoading ? t('loading') : t('noResultDetails')}</p>
        {error && (
          <Button size="xs" variant="outline" onClick={() => void mutate()}>
            {t('retry')}
          </Button>
        )}
      </div>
    )
  }
  return <TaskExecutionDetails conversationId={conversationId} task={resolved} />
}

function TaskExecutionDetails({
  conversationId,
  task,
}: {
  conversationId: string
  task: BackgroundTask
}) {
  const t = useTranslations('backgroundTasks')
  const { workspaceId } = useWorkspaceContext()
  const messages = useMessageStore((s) => s.messages?.[conversationId] ?? EMPTY_MESSAGES)
  const liveCall = useMessageStore((s) =>
    s.streamingConversationId === conversationId
      ? Object.values(s.streamAgents)
          .flatMap((agent) => agent.blocks)
          .find((block) => block.type === 'tool_call' && block.id === task.tool_call_id)
      : undefined,
  )
  const liveResult = useMessageStore((s) =>
    s.streamingConversationId === conversationId ? s.toolResultMap[task.tool_call_id] : undefined,
  )
  const originalCall =
    messages
      .flatMap((message) => message.content)
      .find((block) => block.type === 'tool_call' && block.id === task.tool_call_id) ?? liveCall
  const originalResult = messages.find(
    (message) => message.role === 'tool_result' && message.tool_call_id === task.tool_call_id,
  )
  const args =
    originalCall?.type === 'tool_call'
      ? originalCall.arguments
      : task.details
        ? { command: task.details.command }
        : null
  const name = originalCall?.type === 'tool_call' ? originalCall.name : task.details?.command_kind
  const result =
    originalResult?.role === 'tool_result'
      ? getToolResultPreviewContent(originalResult)
      : (liveResult?.content ?? null)
  const active = ['starting', 'running', 'waiting_input', 'unknown'].includes(task.state)
  const { content, error, loading, refresh } = useSandboxFileContent(
    workspaceId,
    task.details?.log_path || task.result_ref,
    conversationId,
    active ? 5_000 : 0,
  )
  useEffect(() => {
    // A task can finish before the next live-log poll. Fetch its final output.
    if (!active && refresh) void refresh()
  }, [active, task.revision, refresh])

  return (
    <div className="min-w-0 space-y-3 text-xs">
      {name && <p className="break-words font-mono text-foreground">{name}</p>}
      {!originalCall && <p>{t('originalUnavailable')}</p>}
      {args ? <GenericToolView args={args} result={result} /> : null}
      {task.details?.exit_code != null && <p>{t('exitCode', { code: task.details.exit_code })}</p>}
      <p className="font-medium text-muted-foreground">{t('output')}</p>
      {loading ? (
        <p>{t('loadingOutput')}</p>
      ) : error ? (
        <div className="space-y-2">
          <p>{t('outputFailed')}</p>
          <Button size="xs" variant="outline" onClick={() => void refresh()}>
            {t('retry')}
          </Button>
        </div>
      ) : content !== null ? (
        <pre
          className="max-h-96 overflow-auto whitespace-pre-wrap break-all
            rounded bg-sunken p-3 font-mono"
        >
          {content || t('emptyOutput')}
        </pre>
      ) : (
        <p>{t(task.result_readiness === 'unavailable' ? 'outputUnavailable' : 'outputPending')}</p>
      )}
    </div>
  )
}
