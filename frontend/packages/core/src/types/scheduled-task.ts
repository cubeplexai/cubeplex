export type ScheduledTaskStatus = 'active' | 'paused'
export type ScheduleKind = 'cron' | 'interval' | 'once'
export type TargetMode = 'fixed' | 'new_each_run'

export type ScheduledTaskRunState =
  | 'claimed'
  | 'started'
  | 'succeeded'
  | 'failed'
  | 'skipped_missed'
  | 'skipped_busy_max_retries'

export interface ScheduledTaskOut {
  id: string
  name: string
  status: ScheduledTaskStatus
  schedule_kind: ScheduleKind
  cron_expr: string | null
  interval_seconds: number | null
  run_at: string | null
  timezone: string
  prompt: string
  target_mode: TargetMode
  target_conversation_id: string | null
  owner_user_id: string
  next_fire_at: string | null
  last_fired_at: string | null
  created_at: string
  updated_at: string
}

export interface ScheduledTaskRunOut {
  id: string
  scheduled_for: string
  claimed_at: string
  started_at: string | null
  state: ScheduledTaskRunState
  retry_count: number
  next_retry_at: string | null
  run_id: string | null
  conversation_id: string | null
  detail: string | null
}

export interface ScheduledTaskCreate {
  name: string
  prompt: string
  schedule_kind: ScheduleKind
  cron_expr?: string
  interval_seconds?: number
  run_at?: string
  timezone?: string
  target_mode: TargetMode
  target_conversation_id?: string
}

export type ScheduledTaskPatch = Partial<ScheduledTaskCreate>
