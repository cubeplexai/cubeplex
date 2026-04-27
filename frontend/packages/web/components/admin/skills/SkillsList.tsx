'use client'

import type { SkillSummary } from '@cubebox/core'
import { SkillCard } from './SkillCard'

interface SkillsListProps {
  skills: SkillSummary[]
  loading: boolean
  error: Error | undefined
  selectedId: string | null
  onSelect: (id: string) => void
}

export function SkillsList({ skills, loading, error, selectedId, onSelect }: SkillsListProps) {
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
        加载中…
      </div>
    )
  }
  if (error) {
    return (
      <div className="m-3 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
        加载失败：{error.message}
      </div>
    )
  }
  if (skills.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-1 px-6 text-center">
        <p className="text-sm text-muted-foreground">暂无 skill</p>
        <p className="text-xs text-muted-foreground/70">点击右上角 “上传 skill” 添加自定义技能</p>
      </div>
    )
  }
  return (
    <ul className="flex flex-col gap-1.5 p-3">
      {skills.map((skill) => (
        <li key={skill.id}>
          <SkillCard
            skill={skill}
            active={skill.id === selectedId}
            onClick={() => onSelect(skill.id)}
          />
        </li>
      ))}
    </ul>
  )
}
