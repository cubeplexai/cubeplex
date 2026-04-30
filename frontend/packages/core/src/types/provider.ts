export interface Provider {
  id: string
  name: string
  provider_type: string
  base_url: string
  auth_type: 'api_key' | 'oauth' | 'bearer_token' | 'none'
  has_api_key: boolean
  logo_url: string | null
  enabled: boolean
  is_system: boolean
  model_count: number
  models?: Model[]
  org_override?: { enabled: boolean }
  extra_body: Record<string, unknown>
  extra_headers: Record<string, unknown>
  created_by_user_id: string
  created_at: string
  updated_at: string
}

export interface Model {
  id: string
  provider_id: string
  model_id: string
  display_name: string
  reasoning: boolean
  input_modalities: string[]
  cost_input: number
  cost_output: number
  cost_cache_read: number
  cost_cache_write: number
  context_window: number
  max_tokens: number
  extra_body: Record<string, unknown>
  extra_headers: Record<string, unknown>
  enabled: boolean
  is_system: boolean
  created_at: string
  updated_at: string
}

export interface ProviderCreate {
  name: string
  provider_type?: string
  base_url: string
  auth_type?: string
  api_key?: string | null
  logo_url?: string | null
  extra_body?: Record<string, unknown>
  extra_headers?: Record<string, unknown>
}

export interface ProviderUpdate {
  name?: string | null
  provider_type?: string | null
  base_url?: string | null
  auth_type?: string | null
  api_key?: string | null
  logo_url?: string | null
  extra_body?: Record<string, unknown> | null
  extra_headers?: Record<string, unknown> | null
  enabled?: boolean | null
}

export interface ModelCreate {
  model_id: string
  display_name: string
  reasoning?: boolean
  input_modalities?: string[]
  cost_input?: number
  cost_output?: number
  cost_cache_read?: number
  cost_cache_write?: number
  context_window: number
  max_tokens: number
  extra_body?: Record<string, unknown>
  extra_headers?: Record<string, unknown>
}

export interface ModelUpdate {
  display_name?: string | null
  reasoning?: boolean | null
  input_modalities?: string[] | null
  cost_input?: number | null
  cost_output?: number | null
  cost_cache_read?: number | null
  cost_cache_write?: number | null
  context_window?: number | null
  max_tokens?: number | null
  extra_body?: Record<string, unknown> | null
  extra_headers?: Record<string, unknown> | null
  enabled?: boolean | null
}

export interface TestResult {
  ok: boolean
  error: string | null
  latency_ms: number
}

export interface OrgLLMSettings {
  default_model: string | null
  fallback_models: string[]
}

export interface OrgLLMSettingsUpdate {
  default_model?: string | null
  fallback_models?: string[] | null
}
