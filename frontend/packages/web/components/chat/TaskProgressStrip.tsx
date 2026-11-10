'use client'

import { useId, useState } from 'react'
import { useTranslations } from 'next-intl'
import { CheckCircle2, ChevronDown, ChevronRight, Circle, ListChecks, Loader2 } from 'lucide-react'
import { useMessageStore, type TodoItem } from '@cubeplex/core'

import { ComposerOverlayShell } from './ComposerOverlayShell'
import { CHAT_COLUMN_CLASS } from '@/lib/chatLayout'
import { cn } from '@/lib/utils'

const EMPTY_TODOS: TodoItem[] = []

// Open or closed for this browser session. A list that appears after being
// empty opens once. Later status updates do not reopen it.
const expandedPrefs = new Map<string, boolean>()

interface TaskProgressStripProps {
  conversationId: string
  todos: TodoItem[]
}

export function ConversationTaskProgress({ conversationId }: { conversationId: string }) {
  const todos = useMessageStore((s) => s.todosByConversation[conversationId] ?? EMPTY_TODOS)
  return <TaskProgressStrip conversationId={conversationId} todos={todos} />
}

export function TaskProgressStrip({ conversationId, todos }: TaskProgressStripProps) {
  const t = useTranslations('chat')
  const listId = useId()
  const [expanded, setExpanded] = useState(() => expandedPrefs.get(conversationId) ?? false)
  const [showCompleted, setShowCompleted] = useState(false)
  const [trackedConversation, setTrackedConversation] = useState(conversationId)
  const [prevCount, setPrevCount] = useState(todos.length)

  if (trackedConversation !== conversationId) {
    setTrackedConversation(conversationId)
    setExpanded(expandedPrefs.get(conversationId) ?? false)
    setShowCompleted(false)
    setPrevCount(todos.length)
  } else if (todos.length === 0) {
    if (prevCount !== 0 || expanded || showCompleted) {
      expandedPrefs.delete(conversationId)
      setExpanded(false)
      setShowCompleted(false)
      setPrevCount(0)
    }
  } else if (prevCount === 0) {
    expandedPrefs.set(conversationId, true)
    setExpanded(true)
    setPrevCount(todos.length)
  } else if (prevCount !== todos.length) {
    setPrevCount(todos.length)
  }

  if (todos.length === 0) return null

  const completedItems = todos.filter((todo) => todo.status === 'completed')
  const completed = completedItems.length
  const allDone = completed === todos.length
  const current =
    todos.find((todo) => todo.status === 'in_progress') ??
    todos.find((todo) => todo.status === 'pending') ??
    null
  const openItems = todos.filter((todo) => todo.status !== 'completed' && todo !== current)
  const label = allDone
    ? t('tasksDone', { completed, total: todos.length })
    : (current?.description ?? t('tasksProgress'))
  const pct = Math.round((completed / todos.length) * 100)

  function toggleExpanded() {
    setExpanded((open) => {
      const next = !open
      expandedPrefs.set(conversationId, next)
      return next
    })
  }

  return (
    <div className={cn(CHAT_COLUMN_CLASS, 'relative')}>
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={expanded ? listId : undefined}
        onClick={toggleExpanded}
        className="flex min-h-8 w-full items-center gap-2 py-1.5 text-left text-xs
          text-muted-foreground hover:text-foreground
          focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {allDone ? (
          <CheckCircle2 aria-hidden className="size-3.5 shrink-0 text-success-fg" />
        ) : current?.status === 'in_progress' ? (
          <Loader2 aria-hidden className="size-3.5 shrink-0 animate-spin text-primary" />
        ) : (
          <ListChecks aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
        )}
        <span aria-live="polite" className="min-w-0 flex-1 truncate text-foreground">
          {label}
        </span>
        {!allDone && (
          <span className="shrink-0 text-muted-foreground">
            {t('tasksCompleted', { completed, total: todos.length })}
          </span>
        )}
        {expanded ? (
          <ChevronDown aria-hidden className="size-3.5 shrink-0" />
        ) : (
          <ChevronRight aria-hidden className="size-3.5 shrink-0" />
        )}
      </button>
      <div
        className="h-0.5 overflow-hidden rounded-full bg-muted"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={todos.length}
        aria-valuenow={completed}
        aria-label={t('tasksProgress')}
      >
        <div className="h-full bg-primary" style={{ width: `${pct}%` }} />
      </div>
      {expanded && (
        <ComposerOverlayShell id={listId} aria-label={t('tasksProgress')}>
          <div className="overflow-y-auto p-2">
            {openItems.length > 0 && (
              <ul className="space-y-1">
                {openItems.map((todo, index) => (
                  <TodoRow key={`${todo.description}-${index}`} todo={todo} />
                ))}
              </ul>
            )}
            {completedItems.length > 0 && (
              <div className={openItems.length > 0 ? 'mt-1' : undefined}>
                <button
                  type="button"
                  aria-expanded={showCompleted}
                  onClick={() => setShowCompleted((open) => !open)}
                  className="flex min-h-8 w-full items-center gap-2 py-1 text-left text-xs
                    text-muted-foreground hover:text-foreground
                    focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {showCompleted ? (
                    <ChevronDown aria-hidden className="size-3 shrink-0" />
                  ) : (
                    <ChevronRight aria-hidden className="size-3 shrink-0" />
                  )}
                  {t('tasksCompletedGroup', { count: completedItems.length })}
                </button>
                {showCompleted && (
                  <ul className="space-y-1">
                    {completedItems.map((todo, index) => (
                      <TodoRow key={`${todo.description}-${index}`} todo={todo} />
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        </ComposerOverlayShell>
      )}
    </div>
  )
}

function TodoRow({ todo }: { todo: TodoItem }) {
  const done = todo.status === 'completed'
  const active = todo.status === 'in_progress'
  return (
    <li className="flex items-center gap-2 py-0.5 text-xs">
      {done ? (
        <CheckCircle2 aria-hidden className="size-3 shrink-0 text-success-fg" />
      ) : active ? (
        <Loader2 aria-hidden className="size-3 shrink-0 animate-spin text-primary" />
      ) : (
        <Circle aria-hidden className="size-3 shrink-0 text-muted-foreground/40" />
      )}
      <span className={done ? 'text-muted-foreground' : 'text-foreground'}>{todo.description}</span>
    </li>
  )
}
