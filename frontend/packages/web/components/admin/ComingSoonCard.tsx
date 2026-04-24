import { Clock3 } from 'lucide-react'

interface ComingSoonCardProps {
  title: string
  description: string
  backlogRef: string
}

export function ComingSoonCard({ title, description, backlogRef }: ComingSoonCardProps) {
  return (
    <div className="max-w-2xl mx-auto mt-16 px-6">
      <h2 className="text-2xl font-semibold tracking-tight mb-2">{title}</h2>
      <p className="text-muted-foreground mb-8 leading-relaxed">{description}</p>
      <div className="rounded-xl border border-dashed border-border bg-muted/20 px-6 py-10 text-center">
        <div className="mx-auto mb-3 flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <Clock3 className="size-4" />
        </div>
        <p className="text-sm font-medium mb-1">本版本不可用</p>
        <p className="text-xs text-muted-foreground">实现归属：{backlogRef}</p>
      </div>
    </div>
  )
}
