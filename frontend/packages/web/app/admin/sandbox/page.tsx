'use client'

import { PolicyEditor } from './_components/PolicyEditor'

export default function SandboxPolicyPage() {
  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border/70 px-6 py-4">
        <h2 className="text-lg font-semibold tracking-tight">Sandbox policy</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Set the default image and the network / command rules applied to every workspace sandbox
          in this org.
        </p>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto flex max-w-3xl flex-col gap-6">
          <PolicyEditor />
        </div>
      </div>
    </div>
  )
}
