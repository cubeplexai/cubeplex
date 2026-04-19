'use client'

import { createContext, useContext } from 'react'

export interface WorkspaceContextValue {
  workspaceId: string | null
}

export const WorkspaceContext = createContext<WorkspaceContextValue>({ workspaceId: null })

export function useWorkspaceContext(): WorkspaceContextValue {
  return useContext(WorkspaceContext)
}
