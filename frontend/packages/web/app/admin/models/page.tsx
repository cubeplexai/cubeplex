import { ComingSoonCard } from '@/components/admin/ComingSoonCard'

export default function ModelsPage() {
  return (
    <ComingSoonCard
      title="模型管理"
      description="按 provider 列出可用模型，配置组织默认模型与 fallback 链。"
      backlogRef="M2 完整版（v1 后续 spec）"
    />
  )
}
