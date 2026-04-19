import { WorkspaceList } from '@/components/workspace/WorkspaceList'
import { WorkspaceCreateForm } from '@/components/workspace/WorkspaceCreateForm'

export default function WorkspacesPage() {
  return (
    <div className="max-w-2xl mx-auto w-full p-6 space-y-6">
      <h1 className="text-lg font-semibold">Workspaces</h1>
      <WorkspaceList />
      <WorkspaceCreateForm />
    </div>
  )
}
