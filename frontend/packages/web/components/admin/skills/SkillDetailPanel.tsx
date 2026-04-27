'use client'

interface SkillDetailPanelProps {
  skillId: string | null
  onActionDone: () => void
}

export function SkillDetailPanel(_props: SkillDetailPanelProps) {
  return (
    <div className="flex flex-1 items-center justify-center p-8 text-sm text-muted-foreground">
      选择一个 skill 查看详情
    </div>
  )
}
