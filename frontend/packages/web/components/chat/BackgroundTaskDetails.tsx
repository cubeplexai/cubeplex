'use client'

import { useEffect } from 'react'
import useSWR from 'swr'
import { useTranslations } from 'next-intl'
import {
  createApiClient,
  getBackgroundTask,
  getSubagentSummary,
  getToolResultPreviewContent,
  useMessageStore,
} from '@cubeplex/core'
import type { BackgroundTask, Message } from '@cubeplex/core'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { backgroundTaskOutputUrl, useBackgroundTaskOutput } from '@/hooks/useBackgroundTaskOutput'
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
  const subagentSummaries = messages.flatMap((message) => {
    if (message.role !== 'tool_result') return []
    const summary = getSubagentSummary(message)
    return summary ? [summary] : []
  })
  const subagentCall = subagentSummaries
    .flatMap((summary) => summary.tool_calls)
    .find((call) => call.id === task.tool_call_id)
  const subagentResult = subagentSummaries
    .flatMap((summary) => summary.tool_results ?? [])
    .find((result) => result.tool_call_id === task.tool_call_id)
  const originalCall =
    messages
      .flatMap((message) => message.content)
      .find((block) => block.type === 'tool_call' && block.id === task.tool_call_id) ??
    liveCall ??
    (subagentCall ? { ...subagentCall, type: 'tool_call' as const } : undefined)
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
      : (subagentResult?.content ?? liveResult?.content ?? null)
  const active = ['starting', 'running', 'waiting_input', 'unknown'].includes(task.state)
  const unavailable = task.result_readiness === 'unavailable'
  const { content, error, loading, refresh } = useBackgroundTaskOutput(
    workspaceId,
    conversationId,
    unavailable ? null : task.id,
    active ? 5_000 : 0,
  )
  useEffect(() => {
    // A task can finish before the next live-log poll. Fetch its final output.
    if (!active && !unavailable && refresh) void refresh()
  }, [active, unavailable, task.revision, refresh])

  const downloadUrl = workspaceId
    ? `${backgroundTaskOutputUrl(workspaceId, conversationId, task.id)}?download=true`
    : null

  return (
    <div className="min-w-0 space-y-3 text-xs">
      {name && <p className="break-words font-mono text-foreground">{name}</p>}
      {!originalCall && <p>{t('originalUnavailable')}</p>}
      {args ? <GenericToolView args={args} result={result} /> : null}
      {task.details?.exit_code != null && <p>{t('exitCode', { code: task.details.exit_code })}</p>}
      <p className="font-medium text-muted-foreground">{t('output')}</p>
      {unavailable ? (
        <div className="space-y-1">
          <p>{t('outputUnavailable')}</p>
          {task.result_unavailable_reason && <p>{task.result_unavailable_reason}</p>}
        </div>
      ) : loading ? (
        <p>{t('loadingOutput')}</p>
      ) : error instanceof Error && error.message === 'FILE_TOO_LARGE' ? (
        <div className="space-y-2">
          <p>{t('outputTooLarge')}</p>
          {downloadUrl && (
            <a className="text-primary underline" download href={downloadUrl}>
              {t('downloadOutput')}
            </a>
          )}
        </div>
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
        <p>{t('outputPending')}</p>
      )}
    </div>
  )
}
