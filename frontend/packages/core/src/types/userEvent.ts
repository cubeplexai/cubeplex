export interface MemoryUpdateItem {
  op: 'save' | 'update'
  memory_id: string
}

export interface MemoryUpdatedPayload {
  conversation_id: string
  run_id: string
  items: MemoryUpdateItem[]
}

export interface UserEvent {
  id: string
  type: 'memory_updated'
  workspace_id: string | null
  payload: MemoryUpdatedPayload
  created_at: string
}
