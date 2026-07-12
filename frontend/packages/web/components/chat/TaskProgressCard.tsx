'use client'

import { useState, useEffect, useRef } from 'react'
import { useTranslations } from 'next-intl'
import { CheckCircle2, Circle, ChevronDown, ChevronRight, Loader2, ListChecks } from 'lucide-react'
import type { TodoItem } from '@cubeplex/core'

interface TaskProgressCardProps {
  todos: TodoItem[]
  isStreaming: boolean
}

export function TaskProgressCard({ todos, isStreaming }: TaskProgressCardProps) {
  const t = useTranslations('chat')
  const [isExpanded, setIsExpanded] = useState(false)
  const prevStreamingRef = useRef(isStreaming)

  // Auto-collapse when streaming ends
  useEffect(() => {
    if (!isStreaming && prevStreamingRef.current) {
      setIsExpanded(false)
    }
    prevStreamingRef.current = isStreaming
  }, [isStreaming])

  if (todos.length === 0) return null

  const completed = todos.filter((t) => t.status === 'completed').length
  const inProgress = todos.find((t) => t.status === 'in_progress')
  const allDone = completed === todos.length && !isStreaming

  return (
    <div className="bg-card border border-border rounded-xl px-3 py-2.5">
      <button
        type="button"
        onClick={() => setIsExpanded((prev) => !prev)}
        className="flex items-center gap-1.5 text-xs text-muted-foreground
          transition-colors group w-full text-left hover:text-foreground cursor-pointer"
      >
        <span
          className="text-muted-foreground/60 group-hover:text-muted-foreground
          transition-colors"
        >
          {isExpanded ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
        </span>

        {allDone ? (
          <>
            <CheckCircle2 className="size-3 text-success-fg shrink-0" />
            <span className="text-foreground">
              {t('tasksDone', { completed, total: todos.length })}
            </span>
          </>
        ) : isStreaming && inProgress ? (
          <>
            <Loader2 className="size-3 text-primary animate-spin shrink-0" />
            <span className="text-foreground truncate">{inProgress.description}</span>
            <span className="text-muted-foreground/50 ml-auto shrink-0">
              {completed}/{todos.length}
            </span>
          </>
        ) : (
          <>
            <ListChecks className="size-3 text-muted-foreground/70 shrink-0" />
            <span className="text-foreground">
              {t('tasksProgress', { completed, total: todos.length })}
            </span>
          </>
        )}
      </button>

      {isExpanded && (
        <div className="mt-2 space-y-1 pl-1">
          {todos.map((todo, i) => (
            <div key={todo.id ?? i} className="flex items-center gap-2 py-0.5 text-xs">
              {todo.status === 'completed' ? (
                <CheckCircle2 className="size-3 text-success-fg shrink-0" />
              ) : todo.status === 'in_progress' ? (
                <Loader2 className="size-3 text-primary animate-spin shrink-0" />
              ) : (
                <Circle className="size-3 text-muted-foreground/30 shrink-0" />
              )}
              <span
                className={
                  todo.status === 'completed'
                    ? 'text-muted-foreground line-through'
                    : todo.status === 'in_progress'
                      ? 'text-foreground'
                      : 'text-muted-foreground/70'
                }
              >
                {todo.description}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
