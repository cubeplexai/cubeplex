'use client'

import { useState, useEffect, useRef } from 'react'
import { ListChecks, CheckCircle2, Circle, ChevronUp, ChevronDown } from 'lucide-react'
import type { TodoItem } from '@cubeplex/core'

interface TaskProgressBarProps {
  todos: TodoItem[]
}

export function TaskProgressBar({ todos }: TaskProgressBarProps) {
  const [isExpanded, setIsExpanded] = useState(todos.length > 0)

  // Auto-expand when todos first become non-empty
  const prevCount = useRef(todos.length)
  useEffect(() => {
    if (prevCount.current === 0 && todos.length > 0) {
      setIsExpanded(true)
    }
    prevCount.current = todos.length
  }, [todos.length])

  if (todos.length === 0) return null

  const completed = todos.filter((t) => t.status === 'completed').length
  const inProgress = todos.find((t) => t.status === 'in_progress')

  return (
    <div className="bg-card border-t border-border">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex w-full items-center gap-2
          px-4 py-2 text-sm hover:bg-muted/30
          transition-colors"
      >
        <ListChecks
          className="size-3.5 text-muted-foreground
            shrink-0"
        />
        <span className="font-medium text-foreground">
          Task Progress {completed}/{todos.length}
        </span>
        {inProgress && !isExpanded && (
          <span
            className="text-xs text-muted-foreground
              truncate ml-2 flex items-center gap-1"
          >
            <Circle
              className="size-2.5 text-info-fg
                animate-pulse inline-block shrink-0"
            />
            {inProgress.description}
          </span>
        )}
        <span className="ml-auto shrink-0">
          {isExpanded ? (
            <ChevronDown
              className="size-3.5
                text-muted-foreground"
            />
          ) : (
            <ChevronUp
              className="size-3.5
                text-muted-foreground"
            />
          )}
        </span>
      </button>

      {isExpanded && (
        <div className="px-4 pb-2 space-y-1">
          {todos.map((todo, i) => (
            <div
              key={todo.id ?? i}
              className="flex items-center gap-2 py-0.5
                text-sm"
            >
              {todo.status === 'completed' ? (
                <CheckCircle2
                  className="size-3.5 text-success-fg
                    shrink-0"
                />
              ) : todo.status === 'in_progress' ? (
                <Circle
                  className="size-3.5 text-info-fg
                    animate-pulse shrink-0"
                />
              ) : (
                <Circle
                  className="size-3.5
                    text-muted-foreground/30 shrink-0"
                />
              )}
              <span
                className={
                  todo.status === 'completed' ? 'text-muted-foreground' : 'text-foreground'
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
