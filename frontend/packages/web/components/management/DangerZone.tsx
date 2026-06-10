import type { ReactNode } from 'react'

export function DangerZone({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border border-danger-border rounded-lg overflow-hidden">
      <h2 className="px-4 py-2.5 text-sm font-medium text-danger-fg bg-danger-surface border-b border-danger-border">
        {title}
      </h2>
      <div className="p-4">{children}</div>
    </section>
  )
}
