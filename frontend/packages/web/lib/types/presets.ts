export type ThinkingLevel = 'off' | 'low' | 'medium' | 'high' | 'max'
export type ModelTier = 'lite' | 'flash' | 'pro' | 'max'
export type TaskKey = 'title' | 'summarize' | 'compaction'
export const MODEL_TIERS: ModelTier[] = ['lite', 'flash', 'pro', 'max']
export const TASK_KEYS: TaskKey[] = ['title', 'summarize', 'compaction']

export interface TierSetting {
  enabled: boolean
  primary: string | null
  fallbacks: string[]
}

export interface CustomPreset {
  label: string
  primary: string
  fallbacks: string[]
  description: string
}

export interface ModelPresetsConfig {
  tiers: Record<ModelTier, TierSetting>
  custom_presets: CustomPreset[]
  default_preset: string
  task_routing: Partial<Record<TaskKey, string>>
}

export interface WorkspacePresetSummary {
  key: string
  kind: 'tier' | 'custom'
  primary: string
  description: string
  is_default: boolean
  /** Gateway provider slug from primary; optional on older API responses. */
  provider_slug?: string | null
  model_id?: string | null
  model_display_name?: string | null
  context_window?: number | null
  reasoning?: boolean | null
  input_modalities?: string[] | null
}
