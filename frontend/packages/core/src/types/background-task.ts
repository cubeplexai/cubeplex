export type BackgroundTaskState =
  'starting' | 'running' | 'waiting_input' | 'succeeded' | 'failed' | 'cancelled' | 'unknown'

export interface BackgroundTaskCapabilities {
  can_stop: boolean
  remote_cancel_supported: boolean
  reconnect_supported: boolean
  logs_supported: boolean
  input_supported: boolean
}

export interface BackgroundTaskNotification {
  enabled: boolean
  has_pending: boolean
  cancelled_at: string | null
}

export interface CommandTaskDetails {
  type: 'command'
  command_id: string
  command_kind: 'execute' | 'monitor' | string
  command: string
  status: string
  exit_code: number | null
  log_path: string
  log_state: 'pending' | 'retrying' | 'complete' | 'unavailable' | string
  monitor_outcome: 'matched' | 'failed' | 'timed_out' | null
}

export interface BackgroundTask {
  id: string
  kind: string
  description: string
  parent_task_id: string | null
  originating_run_id: string
  tool_call_id: string
  agent_id: string | null
  execution_generation: number
  state: BackgroundTaskState
  deadline_at: string | null
  stop_requested_at: string | null
  stop_reason: string | null
  backgrounded_at: string | null
  finished_at: string | null
  result_summary: string
  result_ref: string | null
  result_readiness: 'pending' | 'ready' | 'unavailable' | string
  result_unavailable_reason: string | null
  revision: number
  created_at: string
  updated_at: string
  cleanup_pending: boolean
  capabilities: BackgroundTaskCapabilities
  notification: BackgroundTaskNotification
  details: CommandTaskDetails | null
}

export interface BackgroundTaskEvent {
  id: string
  task_id: string
  task_kind: string
  execution_generation: number
  reason: string
  summary: string
  result_ref: string | null
  state: 'pending' | 'claimed' | 'delivered' | 'discarded'
  discard_reason: string | null
  revision: number
  created_at: string
  updated_at: string
  delivered_at: string | null
}

export interface BackgroundTaskSummary {
  has_inflight: boolean
  has_pending: boolean
  has_cleanup: boolean
  can_stop: boolean
}

export interface BackgroundTaskEventPage {
  items: BackgroundTaskEvent[]
  next_cursor: string | null
  has_more: boolean
}

export interface RunControlStatus {
  run_id: string
  stop_requested_at: string | null
  cleanup_pending: boolean
  can_stop: boolean
}

export interface StopAllStatus {
  execution_generation: number
  requested_at: string
  cleanup_pending: boolean
}
