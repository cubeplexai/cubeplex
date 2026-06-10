export type ThinkingLevel = 'off' | 'low' | 'medium' | 'high' | 'xhigh'
export type TaskPresetKey = 'title' | 'compaction' | 'summarize'

export interface AdminPresetEntry {
  label: string
  chain: string[]
  is_default: boolean
}

export interface AdminModelPresetsBody {
  presets: AdminPresetEntry[]
  // Partial — backend accepts any subset of {title, compaction, summarize}.
  // Each value must be a label present in `presets[].label`; backend rejects
  // unknown keys and unknown label refs.
  task_presets: Partial<Record<TaskPresetKey, string>>
}

export interface WorkspacePresetSummary {
  label: string
  is_default: boolean
}
