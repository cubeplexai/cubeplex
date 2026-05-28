import { use } from 'react'
import { SandboxStatusCard } from './_components/SandboxStatusCard'

interface PageProps {
  params: Promise<{ wsId: string }>
}

export default function WorkspaceSandboxPage({ params }: PageProps): React.ReactElement {
  const { wsId } = use(params)
  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border/70 px-6 py-4">
        <h2 className="text-lg font-semibold tracking-tight">Sandbox</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Your sandbox in this workspace. Lifecycle is managed automatically by cubebox.
        </p>
      </header>
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto w-full max-w-2xl">
          <SandboxStatusCard wsId={wsId} />
        </div>
      </div>
    </div>
  )
}
