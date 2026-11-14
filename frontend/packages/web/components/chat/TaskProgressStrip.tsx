'use client'

import { useId, useState } from 'react'
import { useTranslations } from 'next-intl'
import { CheckCircle2, ChevronDown, ChevronRight, Circle, ListChecks, Loader2 } from 'lucide-react'
import { useMessageStore, type TodoItem } from '@cubeplex/core'

import { ComposerOverlayShell } from './ComposerOverlayShell'
import { CHAT_COLUMN_CLASS } from '@/lib/chatLayout'
import { cn } from '@/lib/utils'

const EMPTY_TODOS: TodoItem[] = []

// Open or closed for this browser session. Defaults to collapsed; only user
// clicks toggle it, and clearing the list resets the preference.
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
  const [trackedConversation, setTrackedConversation] = useState(conversationId)

  if (trackedConversation !== conversationId) {
    setTrackedConversation(conversationId)
    setExpanded(expandedPrefs.get(conversationId) ?? false)
  } else if (todos.length === 0 && (expanded || expandedPrefs.has(conversationId))) {
    expandedPrefs.delete(conversationId)
    if (expanded) setExpanded(false)
  }

  if (todos.length === 0) return null

  const completed = todos.filter((todo) => todo.status === 'completed').length
  const allDone = completed === todos.length
  const current =
    todos.find((todo) => todo.status === 'in_progress') ??
    todos.find((todo) => todo.status === 'pending') ??
    null
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
          <ul className="min-h-0 space-y-1 overflow-y-auto p-2">
            {todos.map((todo, index) => (
              <TodoRow key={`${todo.id ?? todo.description}-${index}`} todo={todo} />
            ))}
          </ul>
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
