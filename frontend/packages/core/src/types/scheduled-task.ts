export type ScheduledTaskStatus = 'active' | 'paused'
export type ScheduleKind = 'cron' | 'interval' | 'once'
export type TargetMode = 'fixed' | 'new_each_run' | 'im_channel'

export type ScheduledTaskRunState =
  'claimed' | 'started' | 'succeeded' | 'failed' | 'skipped_missed' | 'skipped_busy_max_retries'

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
  /**
   * Topic to inherit when `target_mode === 'new_each_run'`. ``null`` means
   * each run creates a standalone (non-topic) conversation.
   */
  topic_id: string | null
  /** IM destination fields (populated only when `target_mode === 'im_channel'`). */
  im_account_id: string | null
  im_channel_id: string | null
  im_scope_key: string | null
  im_scope_kind: string | null
  owner_user_id: string
  next_fire_at: string | null
  last_fired_at: string | null
  end_at: string | null
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
  target_conversation_id?: string | null
  /** Topic to pin runs into when `target_mode === 'new_each_run'`. */
  topic_id?: string | null
  end_at?: string | null
}

/**
 * PATCH body for an existing scheduled task.
 *
 * Mode-bound destination fields are not partial-PATCHd — use
 * `ScheduledTaskRetarget` / `retargetScheduledTaskDestination` instead.
 * `topic_id` remains patchable only when the row is already `new_each_run`.
 */
export interface ScheduledTaskPatch {
  name?: string
  prompt?: string
  schedule_kind?: ScheduleKind
  cron_expr?: string
  interval_seconds?: number
  run_at?: string
  timezone?: string
  topic_id?: string | null
  end_at?: string | null
}

/**
 * Whole-package destination replacement.
 *
 * `im_channel` resolves IM fields server-side from `anchor_conversation_id`
 * and/or the task's existing conversation/topic binding — do not send im_*.
 */
export interface ScheduledTaskRetarget {
  target_mode: TargetMode
  target_conversation_id?: string | null
  topic_id?: string | null
  anchor_conversation_id?: string | null
}

/** Optional filters accepted by `GET /api/v1/scheduled-tasks`. */
export interface ScheduledTaskListFilters {
  topic_id?: string
  im_account_id?: string
  im_channel_id?: string
}
